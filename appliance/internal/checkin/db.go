package checkin

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// DB wraps a pgx connection pool for checkin queries.
type DB struct {
	pool            *pgxpool.Pool
	serverPublicKey string // Ed25519 public key hex (from SIGNING_PUBLIC_KEY env)
}

// NewDB creates a new DB from a connection string.
func NewDB(ctx context.Context, connString string) (*DB, error) {
	pool, err := pgxpool.New(ctx, connString)
	if err != nil {
		return nil, fmt.Errorf("create pool: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping: %w", err)
	}
	pubKey := os.Getenv("SIGNING_PUBLIC_KEY")
	if pubKey != "" {
		log.Printf("[checkin] Server signing public key loaded (%d chars)", len(pubKey))
	}
	return &DB{pool: pool, serverPublicKey: pubKey}, nil
}

// Close closes the connection pool.
func (db *DB) Close() {
	db.pool.Close()
}

// ValidateAPIKey checks if the provided API key matches the provisioned key for a site.
// Returns true if the key matches, false otherwise.
func (db *DB) ValidateAPIKey(ctx context.Context, siteID, apiKey string) (bool, error) {
	var storedKey *string
	err := db.pool.QueryRow(ctx,
		`SELECT api_key FROM appliance_provisioning WHERE site_id = $1`,
		siteID,
	).Scan(&storedKey)
	if err != nil {
		return false, nil // Site not in provisioning table — deny
	}
	if storedKey == nil || *storedKey == "" {
		return false, nil
	}
	return *storedKey == apiKey, nil
}

// existingAppliance is a row from the dedup query.
type existingAppliance struct {
	ApplianceID  string
	Hostname     string
	MACAddress   string
	FirstCheckin *time.Time
}

// FindExistingAppliances finds appliances with matching MAC or hostname.
// Uses FOR UPDATE to lock rows during the transaction.
func (db *DB) FindExistingAppliances(ctx context.Context, tx pgx.Tx, siteID, macClean, hostnameLower string) ([]existingAppliance, error) {
	rows, err := tx.Query(ctx, `
		SELECT appliance_id, hostname, mac_address, first_checkin
		FROM site_appliances
		WHERE site_id = $1
		AND (
			UPPER(REPLACE(REPLACE(mac_address, ':', ''), '-', '')) = $2
			OR LOWER(hostname) = $3
		)
		ORDER BY last_checkin DESC NULLS LAST
		FOR UPDATE
	`, siteID, macClean, hostnameLower)
	if err != nil {
		return nil, fmt.Errorf("find existing: %w", err)
	}
	defer rows.Close()

	var result []existingAppliance
	for rows.Next() {
		var a existingAppliance
		if err := rows.Scan(&a.ApplianceID, &a.Hostname, &a.MACAddress, &a.FirstCheckin); err != nil {
			return nil, fmt.Errorf("scan: %w", err)
		}
		result = append(result, a)
	}
	return result, rows.Err()
}

// DeleteDuplicates removes duplicate appliance entries.
func (db *DB) DeleteDuplicates(ctx context.Context, tx pgx.Tx, ids []string) error {
	if len(ids) == 0 {
		return nil
	}
	_, err := tx.Exec(ctx, `DELETE FROM site_appliances WHERE appliance_id = ANY($1)`, ids)
	return err
}

// UpsertAppliance creates or updates the canonical appliance entry.
func (db *DB) UpsertAppliance(ctx context.Context, tx pgx.Tx, req CheckinRequest, canonicalID string, firstCheckin, now time.Time) error {
	ipJSON, _ := json.Marshal(req.IPAddresses)

	_, err := tx.Exec(ctx, `
		INSERT INTO site_appliances (
			site_id, appliance_id, hostname, mac_address, ip_addresses,
			agent_version, nixos_version, status, uptime_seconds,
			first_checkin, last_checkin
		) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, 'online', $8, $9, $10)
		ON CONFLICT (appliance_id) DO UPDATE SET
			hostname = EXCLUDED.hostname,
			mac_address = EXCLUDED.mac_address,
			ip_addresses = EXCLUDED.ip_addresses,
			agent_version = EXCLUDED.agent_version,
			nixos_version = EXCLUDED.nixos_version,
			status = 'online',
			uptime_seconds = EXCLUDED.uptime_seconds,
			last_checkin = EXCLUDED.last_checkin
	`, req.SiteID, canonicalID, req.Hostname, NormalizeMAC(req.MACAddress),
		string(ipJSON), req.AgentVersion, req.NixOSVersion,
		req.UptimeSeconds, firstCheckin, now)
	return err
}

// UpdateLegacyAppliance updates the legacy appliances table (non-critical).
func (db *DB) UpdateLegacyAppliance(ctx context.Context, tx pgx.Tx, req CheckinRequest, now time.Time) {
	var ipAddr *string
	if len(req.IPAddresses) > 0 {
		ipAddr = &req.IPAddresses[0]
	}

	_, err := tx.Exec(ctx, `
		UPDATE appliances SET
			last_checkin = $2,
			agent_version = $3,
			nixos_version = $4,
			ip_address = $5::inet,
			status = 'active',
			updated_at = $2
		WHERE site_id = $1
	`, req.SiteID, now, req.AgentVersion, req.NixOSVersion, ipAddr)
	if err != nil {
		log.Printf("[checkin] legacy appliances update failed (non-critical): %v", err)
	}
}

// UpdateAgentPublicKey registers or rotates the Ed25519 agent signing key.
func (db *DB) UpdateAgentPublicKey(ctx context.Context, tx pgx.Tx, siteID, pubKey string) {
	// Check existing
	var existing *string
	err := tx.QueryRow(ctx, `SELECT agent_public_key FROM sites WHERE site_id = $1`, siteID).Scan(&existing)
	if err != nil {
		log.Printf("[checkin] get agent_public_key failed: %v", err)
		return
	}

	if existing != nil && *existing == pubKey {
		return // No change
	}

	if existing != nil && *existing != "" {
		log.Printf("[checkin] WARNING: agent_public_key rotation for site %s (security event)", siteID)
	}

	_, err = tx.Exec(ctx, `UPDATE sites SET agent_public_key = $1 WHERE site_id = $2`, pubKey, siteID)
	if err != nil {
		log.Printf("[checkin] update agent_public_key failed: %v", err)
	}
}

// FetchAdminOrders returns pending admin orders for the appliance.
func (db *DB) FetchAdminOrders(ctx context.Context, tx pgx.Tx, canonicalID string) ([]PendingOrder, error) {
	rows, err := tx.Query(ctx, `
		SELECT order_id, order_type, parameters, priority, created_at, expires_at,
		       nonce, signature, signed_payload
		FROM admin_orders
		WHERE appliance_id = $1
		AND status = 'pending'
		AND expires_at > NOW()
		ORDER BY priority DESC, created_at ASC
	`, canonicalID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var orders []PendingOrder
	for rows.Next() {
		var o PendingOrder
		var params []byte
		var createdAt, expiresAt *time.Time
		var nonce, signature, signedPayload *string

		if err := rows.Scan(&o.OrderID, &o.OrderType, &params, &o.Priority, &createdAt, &expiresAt,
			&nonce, &signature, &signedPayload); err != nil {
			return nil, err
		}
		if params != nil {
			json.Unmarshal(params, &o.Parameters)
		}
		o.CreatedAt = isoTimePtr(createdAt)
		o.ExpiresAt = isoTimePtr(expiresAt)
		if nonce != nil {
			o.Nonce = *nonce
		}
		if signature != nil {
			o.Signature = *signature
		}
		if signedPayload != nil {
			o.SignedPayload = *signedPayload
		}
		orders = append(orders, o)
	}
	return orders, rows.Err()
}

// FetchHealingOrders returns pending healing orders from the orders table.
func (db *DB) FetchHealingOrders(ctx context.Context, tx pgx.Tx, siteID string) ([]PendingOrder, error) {
	rows, err := tx.Query(ctx, `
		SELECT o.order_id, o.runbook_id, o.parameters, o.issued_at, o.expires_at,
		       i.id as incident_id, o.nonce, o.signature, o.signed_payload
		FROM orders o
		JOIN appliances a ON o.appliance_id = a.id
		LEFT JOIN incidents i ON i.order_id = o.id
		WHERE a.site_id = $1
		AND o.status = 'pending'
		AND o.expires_at > NOW()
		ORDER BY o.issued_at ASC
	`, siteID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var orders []PendingOrder
	for rows.Next() {
		var o PendingOrder
		var params []byte
		var issuedAt, expiresAt *time.Time
		var incidentID *int
		var nonce, signature, signedPayload *string

		if err := rows.Scan(&o.OrderID, &o.RunbookID, &params, &issuedAt, &expiresAt, &incidentID,
			&nonce, &signature, &signedPayload); err != nil {
			return nil, err
		}
		o.OrderType = "healing"
		o.Priority = 10 // High priority for healing
		if params != nil {
			json.Unmarshal(params, &o.Parameters)
		}
		if o.Parameters == nil {
			o.Parameters = make(map[string]interface{})
		}
		if incidentID != nil {
			o.Parameters["incident_id"] = *incidentID
		}
		o.CreatedAt = isoTimePtr(issuedAt)
		o.ExpiresAt = isoTimePtr(expiresAt)
		if nonce != nil {
			o.Nonce = *nonce
		}
		if signature != nil {
			o.Signature = *signature
		}
		if signedPayload != nil {
			o.SignedPayload = *signedPayload
		}
		orders = append(orders, o)
	}
	return orders, rows.Err()
}

// FetchFleetOrders returns active fleet-wide orders that this appliance hasn't completed.
// Skips orders where the appliance already matches skip_version.
func (db *DB) FetchFleetOrders(ctx context.Context, tx pgx.Tx, canonicalID, agentVersion string) ([]PendingOrder, error) {
	rows, err := tx.Query(ctx, `
		SELECT fo.id, fo.order_type, fo.parameters, fo.skip_version, fo.created_at, fo.expires_at,
		       fo.nonce, fo.signature, fo.signed_payload
		FROM fleet_orders fo
		WHERE fo.status = 'active'
		AND fo.expires_at > NOW()
		AND NOT EXISTS (
			SELECT 1 FROM fleet_order_completions foc
			WHERE foc.fleet_order_id = fo.id AND foc.appliance_id = $1
		)
		ORDER BY fo.created_at ASC
	`, canonicalID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var orders []PendingOrder
	for rows.Next() {
		var fleetID string
		var orderType string
		var params []byte
		var skipVersion *string
		var createdAt, expiresAt *time.Time
		var nonce, signature, signedPayload *string

		if err := rows.Scan(&fleetID, &orderType, &params, &skipVersion, &createdAt, &expiresAt,
			&nonce, &signature, &signedPayload); err != nil {
			return nil, err
		}

		// Skip if appliance already at target version
		if skipVersion != nil && agentVersion == *skipVersion {
			// Record as skipped
			tx.Exec(ctx, `
				INSERT INTO fleet_order_completions (fleet_order_id, appliance_id, status)
				VALUES ($1, $2, 'skipped')
				ON CONFLICT DO NOTHING
			`, fleetID, canonicalID)
			continue
		}

		var o PendingOrder
		o.OrderID = fmt.Sprintf("fleet::%s::%s", fleetID, canonicalID)
		o.OrderType = orderType
		o.Priority = 5
		if params != nil {
			json.Unmarshal(params, &o.Parameters)
		}
		if o.Parameters == nil {
			o.Parameters = make(map[string]interface{})
		}
		o.Parameters["fleet_order_id"] = fleetID
		o.CreatedAt = isoTimePtr(createdAt)
		o.ExpiresAt = isoTimePtr(expiresAt)
		if nonce != nil {
			o.Nonce = *nonce
		}
		if signature != nil {
			o.Signature = *signature
		}
		if signedPayload != nil {
			o.SignedPayload = *signedPayload
		}
		orders = append(orders, o)
	}
	return orders, rows.Err()
}

// credentialData represents the encrypted_data JSON in site_credentials.
type credentialData struct {
	Host       string  `json:"host"`
	TargetHost string  `json:"target_host"`
	Username   string  `json:"username"`
	Password   string  `json:"password"`
	Domain     string  `json:"domain"`
	UseSSL     bool    `json:"use_ssl"`
	Port       int     `json:"port"`
	PrivateKey string  `json:"private_key"`
	Distro     string  `json:"distro"`
}

// FetchWindowsTargets returns WinRM credentials for the site.
func (db *DB) FetchWindowsTargets(ctx context.Context, tx pgx.Tx, siteID string) ([]WindowsTarget, error) {
	rows, err := tx.Query(ctx, `
		SELECT credential_name, encrypted_data
		FROM site_credentials
		WHERE site_id = $1
		AND credential_type IN ('winrm', 'domain_admin', 'domain_member', 'service_account', 'local_admin')
		ORDER BY created_at DESC
	`, siteID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var targets []WindowsTarget
	for rows.Next() {
		var name string
		var dataJSON []byte
		if err := rows.Scan(&name, &dataJSON); err != nil {
			return nil, err
		}

		var cred credentialData
		if err := json.Unmarshal(dataJSON, &cred); err != nil {
			log.Printf("[checkin] failed to parse credential %s: %v", name, err)
			continue
		}

		hostname := cred.Host
		if hostname == "" {
			hostname = cred.TargetHost
		}
		if hostname == "" {
			continue
		}

		username := cred.Username
		if cred.Domain != "" {
			username = cred.Domain + `\` + cred.Username
		}

		targets = append(targets, WindowsTarget{
			Hostname: hostname,
			Username: username,
			Password: cred.Password,
			UseSSL:   cred.UseSSL,
		})
	}
	return targets, rows.Err()
}

// FetchLinuxTargets returns SSH credentials for the site.
func (db *DB) FetchLinuxTargets(ctx context.Context, tx pgx.Tx, siteID string) ([]LinuxTarget, error) {
	rows, err := tx.Query(ctx, `
		SELECT credential_name, encrypted_data
		FROM site_credentials
		WHERE site_id = $1
		AND credential_type IN ('ssh_password', 'ssh_key')
		ORDER BY created_at DESC
	`, siteID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var targets []LinuxTarget
	for rows.Next() {
		var name string
		var dataJSON []byte
		if err := rows.Scan(&name, &dataJSON); err != nil {
			return nil, err
		}

		var cred credentialData
		if err := json.Unmarshal(dataJSON, &cred); err != nil {
			log.Printf("[checkin] failed to parse SSH credential %s: %v", name, err)
			continue
		}

		hostname := cred.Host
		if hostname == "" {
			hostname = cred.TargetHost
		}
		if hostname == "" {
			continue
		}

		port := cred.Port
		if port == 0 {
			port = 22
		}
		username := cred.Username
		if username == "" {
			username = "root"
		}

		target := LinuxTarget{
			Hostname: hostname,
			Port:     port,
			Username: username,
		}
		if cred.Password != "" {
			target.Password = &cred.Password
		}
		if cred.PrivateKey != "" {
			target.PrivateKey = &cred.PrivateKey
		}
		if cred.Distro != "" {
			target.Distro = &cred.Distro
		}

		targets = append(targets, target)
	}
	return targets, rows.Err()
}

// FetchEnabledRunbooks returns the list of enabled runbook IDs.
func (db *DB) FetchEnabledRunbooks(ctx context.Context, tx pgx.Tx, siteID, canonicalID string) ([]string, error) {
	rows, err := tx.Query(ctx, `
		SELECT
			r.runbook_id,
			COALESCE(
				arc.enabled,
				src.enabled,
				true
			) as enabled
		FROM runbooks r
		LEFT JOIN site_runbook_config src ON src.runbook_id = r.runbook_id AND src.site_id = $1
		LEFT JOIN appliance_runbook_config arc ON arc.runbook_id = r.runbook_id AND arc.appliance_id = $2
		ORDER BY r.runbook_id
	`, siteID, canonicalID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var runbooks []string
	for rows.Next() {
		var id string
		var enabled bool
		if err := rows.Scan(&id, &enabled); err != nil {
			return nil, err
		}
		if enabled {
			runbooks = append(runbooks, id)
		}
	}
	return runbooks, rows.Err()
}

// triggerFlags holds one-time trigger flags.
type triggerFlags struct {
	Enumeration   bool
	ImmediateScan bool
}

// FetchAndClearTriggers fetches trigger flags and clears them atomically.
func (db *DB) FetchAndClearTriggers(ctx context.Context, tx pgx.Tx, canonicalID string) triggerFlags {
	var flags triggerFlags

	err := tx.QueryRow(ctx, `
		SELECT COALESCE(trigger_enumeration, false), COALESCE(trigger_immediate_scan, false)
		FROM site_appliances
		WHERE appliance_id = $1
	`, canonicalID).Scan(&flags.Enumeration, &flags.ImmediateScan)
	if err != nil {
		return flags
	}

	if flags.Enumeration || flags.ImmediateScan {
		_, _ = tx.Exec(ctx, `
			UPDATE site_appliances
			SET trigger_enumeration = false, trigger_immediate_scan = false
			WHERE appliance_id = $1
		`, canonicalID)
	}

	return flags
}

// FetchL2Mode returns the L2 healing mode for the appliance ('auto', 'manual', 'disabled').
func (db *DB) FetchL2Mode(ctx context.Context, tx pgx.Tx, canonicalID string) string {
	var mode *string
	err := tx.QueryRow(ctx, `
		SELECT l2_mode FROM site_appliances WHERE appliance_id = $1
	`, canonicalID).Scan(&mode)
	if err != nil || mode == nil {
		return "auto" // Default
	}
	return *mode
}

// FetchSubscriptionStatus returns the partner's subscription status for the given site.
func (db *DB) FetchSubscriptionStatus(ctx context.Context, tx pgx.Tx, siteID string) string {
	var status *string
	err := tx.QueryRow(ctx, `
		SELECT COALESCE(p.subscription_status, 'none')
		FROM sites s
		LEFT JOIN partners p ON s.partner_id = p.id
		WHERE s.site_id = $1
	`, siteID).Scan(&status)
	if err != nil || status == nil {
		return "active" // Default to active if no partner link (standalone install)
	}
	return *status
}

// BeginTx starts a transaction.
func (db *DB) BeginTx(ctx context.Context) (pgx.Tx, error) {
	return db.pool.Begin(ctx)
}

// ProcessCheckin executes the full checkin pipeline in a single transaction.
func (db *DB) ProcessCheckin(ctx context.Context, req CheckinRequest) (*CheckinResponse, error) {
	now := time.Now().UTC()
	macClean := CleanMAC(req.MACAddress)
	hostnameLower := strings.ToLower(req.Hostname)
	canonicalID := CanonicalApplianceID(req.SiteID, req.MACAddress)

	tx, err := db.BeginTx(ctx)
	if err != nil {
		return nil, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback(ctx)

	// Step 1: Find existing appliances (dedup)
	existing, err := db.FindExistingAppliances(ctx, tx, req.SiteID, macClean, hostnameLower)
	if err != nil {
		return nil, fmt.Errorf("find existing: %w", err)
	}

	// Step 2: Determine first_checkin and merge duplicates
	firstCheckin := now
	var mergeFromIDs []string
	for _, a := range existing {
		if a.ApplianceID != canonicalID {
			mergeFromIDs = append(mergeFromIDs, a.ApplianceID)
		}
		if a.FirstCheckin != nil && a.FirstCheckin.Before(firstCheckin) {
			firstCheckin = *a.FirstCheckin
		}
	}

	if len(mergeFromIDs) > 0 {
		log.Printf("[checkin] Merging %d duplicate appliances into %s", len(mergeFromIDs), canonicalID)
		if err := db.DeleteDuplicates(ctx, tx, mergeFromIDs); err != nil {
			return nil, fmt.Errorf("delete duplicates: %w", err)
		}
	}

	// Step 3: Upsert canonical appliance
	if err := db.UpsertAppliance(ctx, tx, req, canonicalID, firstCheckin, now); err != nil {
		return nil, fmt.Errorf("upsert: %w", err)
	}

	// Step 3.5: Update legacy appliances table (non-critical)
	db.UpdateLegacyAppliance(ctx, tx, req, now)

	// Step 3.6: Update agent public key if provided
	if req.AgentPublicKey != nil && *req.AgentPublicKey != "" {
		db.UpdateAgentPublicKey(ctx, tx, req.SiteID, *req.AgentPublicKey)
	}

	// Step 4: Fetch pending orders (admin + healing)
	var pendingOrders []PendingOrder

	adminOrders, err := db.FetchAdminOrders(ctx, tx, canonicalID)
	if err != nil {
		log.Printf("[checkin] admin orders query failed: %v", err)
	} else {
		pendingOrders = append(pendingOrders, adminOrders...)
	}

	healingOrders, err := db.FetchHealingOrders(ctx, tx, req.SiteID)
	if err != nil {
		log.Printf("[checkin] healing orders query failed: %v", err)
	} else {
		pendingOrders = append(pendingOrders, healingOrders...)
	}

	// Step 4.5: Fetch fleet-wide orders
	agentVer := ""
	if req.AgentVersion != nil {
		agentVer = *req.AgentVersion
	}
	fleetOrders, err := db.FetchFleetOrders(ctx, tx, canonicalID, agentVer)
	if err != nil {
		log.Printf("[checkin] fleet orders query failed: %v", err)
	} else {
		pendingOrders = append(pendingOrders, fleetOrders...)
	}

	// Step 5: Fetch credentials (conditional)
	var windowsTargets []WindowsTarget
	var linuxTargets []LinuxTarget

	if !req.HasLocalCredentials {
		wt, err := db.FetchWindowsTargets(ctx, tx, req.SiteID)
		if err != nil {
			log.Printf("[checkin] windows creds query failed: %v", err)
		} else {
			windowsTargets = wt
		}

		lt, err := db.FetchLinuxTargets(ctx, tx, req.SiteID)
		if err != nil {
			log.Printf("[checkin] linux creds query failed: %v", err)
		} else {
			linuxTargets = lt
		}
	}

	// Step 6: Fetch enabled runbooks
	enabledRunbooks, err := db.FetchEnabledRunbooks(ctx, tx, req.SiteID, canonicalID)
	if err != nil {
		log.Printf("[checkin] runbooks query failed: %v", err)
	}

	// Step 7: Fetch and clear triggers
	flags := db.FetchAndClearTriggers(ctx, tx, canonicalID)

	// Step 8: Fetch L2 healing mode
	l2Mode := db.FetchL2Mode(ctx, tx, canonicalID)

	// Step 9: Fetch subscription status for healing gating
	subStatus := db.FetchSubscriptionStatus(ctx, tx, req.SiteID)

	// Commit transaction
	if err := tx.Commit(ctx); err != nil {
		return nil, fmt.Errorf("commit: %w", err)
	}

	// Ensure non-nil slices for JSON
	if pendingOrders == nil {
		pendingOrders = []PendingOrder{}
	}
	if windowsTargets == nil {
		windowsTargets = []WindowsTarget{}
	}
	if linuxTargets == nil {
		linuxTargets = []LinuxTarget{}
	}
	if enabledRunbooks == nil {
		enabledRunbooks = []string{}
	}

	return &CheckinResponse{
		Status:               "ok",
		ApplianceID:          canonicalID,
		ServerTime:           isoTime(now),
		ServerPublicKey:      db.serverPublicKey,
		MergedDuplicates:     len(mergeFromIDs),
		PendingOrders:        pendingOrders,
		WindowsTargets:       windowsTargets,
		LinuxTargets:         linuxTargets,
		EnabledRunbooks:      enabledRunbooks,
		TriggerEnumeration:   flags.Enumeration,
		TriggerImmediateScan: flags.ImmediateScan,
		L2Mode:               l2Mode,
		SubscriptionStatus:   subStatus,
	}, nil
}

package daemon

import (
	"encoding/json"
	"log"
	"net"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/osiriscare/appliance/internal/maputil"
)

// StateManager holds all mutex-protected state that was formerly embedded in
// Daemon. It implements TargetProvider, CooldownManager, and CheckConfig so
// subsystems can access state through narrow interfaces.
type StateManager struct {
	// Drift report cooldown: prevents excessive incident creation
	cooldownMu sync.Mutex
	cooldowns  map[string]*driftCooldown // key: "hostname:check_type"

	// Linux targets from checkin response
	linuxTargetsMu sync.RWMutex
	linuxTargets   []linuxTarget

	// All Windows targets by hostname (for per-workstation credential lookup)
	winTargetsMu sync.RWMutex
	winTargets   map[string]winTarget

	// L2 mode: "auto" (execute immediately), "manual" (queue for approval), "disabled" (L1 only)
	l2ModeMu sync.RWMutex
	l2Mode   string

	// Subscription status: gates healing operations
	subscriptionMu     sync.RWMutex
	subscriptionStatus string // "active", "trialing", "past_due", "canceled", "none"

	// WinRM port cache: probed once per host, 5986 (SSL) preferred, 5985 (HTTP) fallback
	winrmMu    sync.Mutex
	winrmCache map[string]winrmSettings

	// Pending app discovery results to send in next checkin
	discoveryMu      sync.Mutex
	discoveryResults map[string]interface{}

	// Drift check types disabled by site config (received from checkin response)
	disabledChecks   map[string]bool
	disabledChecksMu sync.RWMutex

	// Pending deploy results to report on the next checkin
	pendingDeployMu      sync.Mutex
	pendingDeployResults []DeployResult

	// gpoFixDone tracks whether the GPO firewall fix has been applied per DC.
	// key = DC hostname, value = true
	gpoFixDone sync.Map

	// adHostnamesFn is a callback to get AD hostnames from the deployer.
	// Set after the deployer is created via SetADHostnamesFunc.
	adHostnamesFn func() map[string]bool
}

// NewStateManager creates a StateManager with all maps initialised.
func NewStateManager() *StateManager {
	return &StateManager{
		cooldowns:      make(map[string]*driftCooldown),
		winTargets:     make(map[string]winTarget),
		disabledChecks: make(map[string]bool),
	}
}

// ---------------------------------------------------------------------------
// TargetProvider implementation
// ---------------------------------------------------------------------------

// LookupWinTarget returns workstation-specific credentials if available.
func (sm *StateManager) LookupWinTarget(hostname string) (winTarget, bool) {
	sm.winTargetsMu.RLock()
	defer sm.winTargetsMu.RUnlock()
	t, ok := sm.winTargets[hostname]
	return t, ok
}

// GetLinuxTargets returns a copy of the current linux targets slice.
func (sm *StateManager) GetLinuxTargets() []linuxTarget {
	sm.linuxTargetsMu.RLock()
	defer sm.linuxTargetsMu.RUnlock()
	result := make([]linuxTarget, len(sm.linuxTargets))
	copy(result, sm.linuxTargets)
	return result
}

// FindCredentialsForHost searches the stored linux and windows targets
// for credentials matching the given hostname or IP address.
// Returns nil if no matching credentials are found.
func (sm *StateManager) FindCredentialsForHost(hostname, ip string) *HostCredentials {
	// Check linux targets first (SSH key auth preferred)
	sm.linuxTargetsMu.RLock()
	for _, t := range sm.linuxTargets {
		if t.Hostname == hostname || t.Hostname == ip {
			creds := &HostCredentials{Username: t.Username}
			if t.PrivateKey != "" {
				creds.SSHKey = t.PrivateKey
			}
			if t.Password != "" {
				creds.Password = t.Password
			}
			sm.linuxTargetsMu.RUnlock()
			return creds
		}
	}
	sm.linuxTargetsMu.RUnlock()

	// Fall back to windows targets (WinRM password auth)
	sm.winTargetsMu.RLock()
	defer sm.winTargetsMu.RUnlock()
	if t, ok := sm.winTargets[hostname]; ok {
		return &HostCredentials{Username: t.Username, Password: t.Password}
	}
	if t, ok := sm.winTargets[ip]; ok {
		return &HostCredentials{Username: t.Username, Password: t.Password}
	}
	return nil
}

// ProbeWinRMPort checks which WinRM port is available on a host.
// Prefers 5986 (HTTPS), falls back to 5985 (HTTP). Results are cached.
func (sm *StateManager) ProbeWinRMPort(hostname string) winrmSettings {
	sm.winrmMu.Lock()
	if sm.winrmCache == nil {
		sm.winrmCache = make(map[string]winrmSettings)
	}
	if cached, ok := sm.winrmCache[hostname]; ok {
		sm.winrmMu.Unlock()
		return cached
	}
	sm.winrmMu.Unlock()

	result := winrmSettings{Port: 5986, UseSSL: true} // default
	dialer := net.Dialer{Timeout: 3 * time.Second}
	conn, err := dialer.Dial("tcp", net.JoinHostPort(hostname, "5986"))
	if err == nil {
		conn.Close()
	} else {
		conn2, err2 := dialer.Dial("tcp", net.JoinHostPort(hostname, "5985"))
		if err2 == nil {
			conn2.Close()
			result = winrmSettings{Port: 5985, UseSSL: false}
			log.Printf("[daemon] WinRM: %s using HTTP (5985) — HTTPS unavailable", hostname)
		}
	}

	sm.winrmMu.Lock()
	sm.winrmCache[hostname] = result
	sm.winrmMu.Unlock()
	return result
}

// GetWinTargets returns a copy of all Windows targets keyed by hostname.
func (sm *StateManager) GetWinTargets() map[string]winTarget {
	sm.winTargetsMu.RLock()
	defer sm.winTargetsMu.RUnlock()
	result := make(map[string]winTarget, len(sm.winTargets))
	for k, v := range sm.winTargets {
		result[k] = v
	}
	return result
}

// GetADHostnames returns a copy of the cached AD host set from the deployer
// callback. Returns nil if no callback is registered or no enumeration has
// completed.
func (sm *StateManager) GetADHostnames() map[string]bool {
	if sm.adHostnamesFn == nil {
		return nil
	}
	return sm.adHostnamesFn()
}

// SetADHostnamesFunc registers the callback used by GetADHostnames.
func (sm *StateManager) SetADHostnamesFunc(fn func() map[string]bool) {
	sm.adHostnamesFn = fn
}

// ---------------------------------------------------------------------------
// CooldownManager implementation
// ---------------------------------------------------------------------------

// ShouldSuppress checks if a drift report should be suppressed due to cooldown.
// Returns true if the drift should be suppressed (still in cooldown).
// Implements flap detection: if >3 drift events for the same key within 30
// minutes, extends cooldown to 1 hour.
func (sm *StateManager) ShouldSuppress(key string) bool {
	sm.cooldownMu.Lock()
	defer sm.cooldownMu.Unlock()

	now := time.Now()

	// Proactive cleanup of stale entries every call (cheap: map iteration)
	for k, entry := range sm.cooldowns {
		if now.Sub(entry.lastSeen) > cooldownCleanup {
			delete(sm.cooldowns, k)
		}
	}

	entry, exists := sm.cooldowns[key]
	if !exists {
		// First time seeing this drift — allow it, start tracking
		sm.cooldowns[key] = &driftCooldown{
			lastSeen:    now,
			count:       1,
			cooldownDur: defaultCooldown,
		}
		return false
	}

	elapsed := now.Sub(entry.lastSeen)

	// Still in cooldown — suppress
	if elapsed < entry.cooldownDur {
		// Count flap occurrences
		if elapsed < flapWindow {
			entry.count++
			if entry.count >= flapThreshold {
				entry.cooldownDur = flapCooldown
				log.Printf("[daemon] Flap detected for %s (%d in %v), cooldown extended to %v",
					key, entry.count, elapsed.Round(time.Second), flapCooldown)
			}
		}
		return true
	}

	// Cooldown expired — allow, reset tracking
	entry.lastSeen = now
	entry.count = 1
	entry.cooldownDur = defaultCooldown
	return false
}

// ---------------------------------------------------------------------------
// CheckConfig implementation
// ---------------------------------------------------------------------------

// IsDisabled returns true if the given check type has been disabled in site
// drift config.
func (sm *StateManager) IsDisabled(checkType string) bool {
	sm.disabledChecksMu.RLock()
	defer sm.disabledChecksMu.RUnlock()
	return sm.disabledChecks[checkType]
}

// GetDisabledChecks returns a copy of the disabled checks map.
func (sm *StateManager) GetDisabledChecks() map[string]bool {
	sm.disabledChecksMu.RLock()
	defer sm.disabledChecksMu.RUnlock()
	result := make(map[string]bool, len(sm.disabledChecks))
	for k, v := range sm.disabledChecks {
		result[k] = v
	}
	return result
}

// ---------------------------------------------------------------------------
// State mutation methods (used by Daemon during checkin processing)
// ---------------------------------------------------------------------------

// SetLinuxTargets replaces the current linux targets.
func (sm *StateManager) SetLinuxTargets(targets []linuxTarget) {
	sm.linuxTargetsMu.Lock()
	sm.linuxTargets = targets
	sm.linuxTargetsMu.Unlock()
}

// LoadWindowsTargets extracts DC/workstation credentials from the checkin
// response and populates the winTargets map plus updates the Config DC fields.
// Prefers the domain_admin role target as DC; falls back to first valid target.
func (sm *StateManager) LoadWindowsTargets(targets []map[string]interface{}, config *Config) {
	var dcHost, dcUser, dcPass string
	allTargets := make(map[string]winTarget, len(targets))

	// Two passes: first look for domain_admin, then fall back to first valid
	for _, t := range targets {
		hostname := maputil.String(t, "hostname")
		username := maputil.String(t, "username")
		password := maputil.String(t, "password")
		role := maputil.String(t, "role")
		useSSL := maputil.Bool(t, "use_ssl")
		if hostname == "" || username == "" {
			continue
		}

		allTargets[hostname] = winTarget{
			Hostname: hostname,
			Username: username,
			Password: password,
			UseSSL:   useSSL,
			Role:     role,
		}

		if role == "domain_admin" {
			dcHost, dcUser, dcPass = hostname, username, password
		}
		// Remember first valid as fallback
		if dcHost == "" {
			dcHost, dcUser, dcPass = hostname, username, password
		}
	}

	// Store all targets for per-workstation lookup
	sm.winTargetsMu.Lock()
	sm.winTargets = allTargets
	sm.winTargetsMu.Unlock()

	if dcHost == "" {
		return
	}

	prev := ""
	if config.DomainController != nil {
		prev = *config.DomainController
	}
	config.DomainController = &dcHost
	config.DCUsername = &dcUser
	config.DCPassword = &dcPass

	if prev != dcHost {
		log.Printf("[daemon] Windows credentials loaded: dc=%s user=%s targets=%d", dcHost, dcUser, len(allTargets))
	}
}

// SetL2Mode sets the L2 healing mode ("auto", "manual", "disabled").
func (sm *StateManager) SetL2Mode(mode string) {
	sm.l2ModeMu.Lock()
	defer sm.l2ModeMu.Unlock()
	if sm.l2Mode != mode {
		log.Printf("[daemon] L2 mode changed: %s → %s", sm.l2Mode, mode)
	}
	sm.l2Mode = mode
}

// GetL2Mode returns the current L2 healing mode.
func (sm *StateManager) GetL2Mode() string {
	sm.l2ModeMu.RLock()
	defer sm.l2ModeMu.RUnlock()
	return sm.l2Mode
}

// SetSubscriptionStatus sets the subscription status and logs changes.
func (sm *StateManager) SetSubscriptionStatus(status string) {
	sm.subscriptionMu.Lock()
	defer sm.subscriptionMu.Unlock()
	if sm.subscriptionStatus != status {
		log.Printf("[daemon] Subscription status changed: %s → %s", sm.subscriptionStatus, status)
	}
	sm.subscriptionStatus = status
}

// IsSubscriptionActive returns true if healing should be allowed.
// Active and trialing subscriptions allow healing; all other states suppress it.
func (sm *StateManager) IsSubscriptionActive() bool {
	sm.subscriptionMu.RLock()
	defer sm.subscriptionMu.RUnlock()
	return sm.subscriptionStatus == "" || sm.subscriptionStatus == "active" || sm.subscriptionStatus == "trialing"
}

// SetDisabledChecks replaces the disabled checks map.
func (sm *StateManager) SetDisabledChecks(checks map[string]bool) {
	sm.disabledChecksMu.Lock()
	sm.disabledChecks = checks
	sm.disabledChecksMu.Unlock()
}

// AddDiscoveryResult adds a discovery result to be sent in the next checkin.
func (sm *StateManager) AddDiscoveryResult(key string, val interface{}) {
	sm.discoveryMu.Lock()
	if sm.discoveryResults == nil {
		sm.discoveryResults = make(map[string]interface{})
	}
	sm.discoveryResults[key] = val
	sm.discoveryMu.Unlock()
}

// DrainDiscoveryResults returns and clears all pending discovery results.
func (sm *StateManager) DrainDiscoveryResults() map[string]interface{} {
	sm.discoveryMu.Lock()
	defer sm.discoveryMu.Unlock()
	result := sm.discoveryResults
	sm.discoveryResults = nil
	return result
}

// AddDeployResult appends a deploy result for the next checkin.
func (sm *StateManager) AddDeployResult(r DeployResult) {
	sm.pendingDeployMu.Lock()
	sm.pendingDeployResults = append(sm.pendingDeployResults, r)
	sm.pendingDeployMu.Unlock()
}

// DrainDeployResults returns and clears all pending deploy results.
func (sm *StateManager) DrainDeployResults() []DeployResult {
	sm.pendingDeployMu.Lock()
	defer sm.pendingDeployMu.Unlock()
	result := sm.pendingDeployResults
	sm.pendingDeployResults = nil
	return result
}

// ---------------------------------------------------------------------------
// State persistence
// ---------------------------------------------------------------------------

// SaveToDisk persists critical in-memory state to disk using atomic write
// (tmp + rename) for crash safety.
func (sm *StateManager) SaveToDisk(stateDir string) {
	sm.linuxTargetsMu.RLock()
	targets := make([]linuxTarget, len(sm.linuxTargets))
	copy(targets, sm.linuxTargets)
	sm.linuxTargetsMu.RUnlock()

	sm.l2ModeMu.RLock()
	l2 := sm.l2Mode
	sm.l2ModeMu.RUnlock()

	sm.subscriptionMu.RLock()
	sub := sm.subscriptionStatus
	sm.subscriptionMu.RUnlock()

	// Snapshot cooldown state
	sm.cooldownMu.Lock()
	cooldowns := make(map[string]persistedCooldown, len(sm.cooldowns))
	for k, v := range sm.cooldowns {
		cooldowns[k] = persistedCooldown{
			LastSeen:    v.lastSeen,
			Count:       v.count,
			CooldownDur: v.cooldownDur,
		}
	}
	sm.cooldownMu.Unlock()

	state := PersistedState{
		LinuxTargets:       targets,
		L2Mode:             l2,
		SubscriptionStatus: sub,
		Cooldowns:          cooldowns,
		SavedAt:            time.Now(),
	}

	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		log.Printf("[daemon] Failed to marshal state: %v", err)
		return
	}

	path := filepath.Join(stateDir, stateFileName)
	tmpPath := path + ".tmp"
	if err := os.WriteFile(tmpPath, data, 0600); err != nil {
		log.Printf("[daemon] Failed to write state file: %v", err)
		return
	}
	if err := os.Rename(tmpPath, path); err != nil {
		log.Printf("[daemon] Failed to rename state file: %v", err)
	}
}

// LoadFromDisk restores persisted state from disk.
// Returns nil error if no state file exists (first boot).
func (sm *StateManager) LoadFromDisk(stateDir string) error {
	saved, err := loadState(stateDir)
	if err != nil {
		return err
	}
	if saved == nil {
		return nil
	}

	sm.linuxTargetsMu.Lock()
	sm.linuxTargets = saved.LinuxTargets
	sm.linuxTargetsMu.Unlock()

	sm.l2ModeMu.Lock()
	sm.l2Mode = saved.L2Mode
	sm.l2ModeMu.Unlock()

	sm.subscriptionMu.Lock()
	sm.subscriptionStatus = saved.SubscriptionStatus
	sm.subscriptionMu.Unlock()

	// Restore cooldowns, pruning any that have expired during downtime
	now := time.Now()
	restored := 0
	sm.cooldownMu.Lock()
	for k, pc := range saved.Cooldowns {
		if now.Sub(pc.LastSeen) < cooldownCleanup {
			sm.cooldowns[k] = &driftCooldown{
				lastSeen:    pc.LastSeen,
				count:       pc.Count,
				cooldownDur: pc.CooldownDur,
			}
			restored++
		}
	}
	sm.cooldownMu.Unlock()

	log.Printf("[daemon] Restored state from disk: %d linux_targets, l2=%s, sub=%s, cooldowns=%d (saved %s ago)",
		len(saved.LinuxTargets), saved.L2Mode, saved.SubscriptionStatus, restored, time.Since(saved.SavedAt).Round(time.Second))

	return nil
}

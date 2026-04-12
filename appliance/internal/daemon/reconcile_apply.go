// Package daemon reconcile-plan application (Session 205 Phase 3).
//
// When Central Command detects an agent that has woken in a past state
// (VM snapshot revert, backup restore, disk clone), it returns a signed
// ReconcilePlan inline in the checkin response. This file handles
// verification + application of that plan.
//
// Security properties enforced here:
//   1. Ed25519 signature verification against the server public key
//      (same key used for all fleet orders). A reconcile plan that
//      fails verification is discarded — NO state changes, NO ack.
//   2. Plan freshness — plans older than 10 minutes are rejected to
//      prevent replay of captured plans.
//   3. Appliance ID scoping — plans addressed to a different appliance
//      are rejected even if the signature is valid.
//   4. Runbook registry gate — unknown runbook_ids are skipped, not
//      errored. Backend can ship a list that includes IDs newer than
//      this binary version.
//
// On success, the handler:
//   - Purges the local nonce cache (orders processor). Nonces from
//     before the epoch rotation become unreplayable.
//   - Writes the new generation_uuid to /var/lib/msp/generation_uuid.
//     Next cycle's Detect() will see it matches CC's, clearing the
//     generation_mismatch signal.
//   - Touches the LKG marker so next-cycle uptime + mtime checks are
//     rebaselined cleanly.
//   - POSTs an ack to /api/appliances/reconcile/ack with outcome.
package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"time"
)

// maxReconcilePlanAge is the oldest a plan may be before we refuse to
// apply it. Prevents replay of captured plans — CC signed the plan just
// now; if it's sitting in a captured packet, we don't want to apply it
// minutes/hours/days later when the world has moved on.
const maxReconcilePlanAge = 10 * time.Minute

// applyReconcilePlan is called from runCheckin when resp.ReconcilePlan
// is non-nil. It does full verification + application + ack in-band.
// Errors are logged but do NOT propagate — a bad plan gets dropped
// silently (the audit row on CC will show plan_status='pending' forever,
// which is a detection signal in itself).
func (d *Daemon) applyReconcilePlan(ctx context.Context, plan *ReconcilePlan) {
	if plan == nil {
		return
	}

	slog.Warn("reconcile plan received from Central Command",
		"component", "reconcile",
		"plan_id", plan.PlanID,
		"runbook_count", len(plan.RunbookIDs),
		"issued_at", plan.IssuedAt)

	// 1. Structural validation — cheap checks before crypto
	if plan.SignatureHex == "" || plan.NewGenerationUUID == "" ||
		plan.NonceEpochHex == "" || plan.ApplianceID == "" {
		slog.Error("reconcile plan missing required fields — rejecting",
			"component", "reconcile", "plan_id", plan.PlanID)
		return
	}

	// 2. Appliance scoping — reject plans for a different appliance.
	// Also catches an attacker replaying a plan issued to a sibling in
	// the same site (each plan targets a specific appliance_id).
	if d.orderProc != nil && d.orderProc.ApplianceID() != "" &&
		plan.ApplianceID != d.orderProc.ApplianceID() {
		slog.Error("reconcile plan appliance_id mismatch — rejecting",
			"component", "reconcile",
			"plan_appliance_id", plan.ApplianceID,
			"our_appliance_id", d.orderProc.ApplianceID())
		return
	}

	// 3. Freshness — reject plans older than 10 minutes.
	//
	// SECURITY: we must check freshness against the issued_at value that
	// is INSIDE the signed payload, NOT the envelope field. An attacker
	// who captures a valid plan can freely mutate the envelope issued_at
	// to "now" and replay, but cannot alter the signed-payload issued_at
	// without invalidating the signature.
	//
	// We also cross-check that the envelope matches the signed value so
	// a future code path that trusts the envelope (e.g. display in UI)
	// cannot be tricked either.
	signedIssuedAt := extractFieldFromSignedPayload(plan.SignedPayload, "issued_at")
	if signedIssuedAt == "" {
		slog.Error("reconcile plan: issued_at missing from signed_payload — rejecting",
			"component", "reconcile", "plan_id", plan.PlanID)
		return
	}
	if signedIssuedAt != plan.IssuedAt {
		slog.Error("reconcile plan: envelope issued_at differs from signed value — rejecting",
			"component", "reconcile",
			"plan_id", plan.PlanID,
			"envelope_issued_at", plan.IssuedAt,
			"signed_issued_at", signedIssuedAt)
		return
	}
	issuedAt, err := time.Parse(time.RFC3339, signedIssuedAt)
	if err != nil {
		slog.Error("reconcile plan signed issued_at unparseable — rejecting",
			"component", "reconcile",
			"plan_id", plan.PlanID,
			"issued_at_raw", signedIssuedAt,
			"error", err)
		return
	}
	age := time.Since(issuedAt)
	if age > maxReconcilePlanAge {
		slog.Error("reconcile plan too old — rejecting",
			"component", "reconcile",
			"plan_id", plan.PlanID,
			"age", age.String(),
			"max_age", maxReconcilePlanAge.String())
		return
	}
	// Also reject future-dated plans — indicates clock skew or forgery.
	if age < -maxReconcilePlanAge {
		slog.Error("reconcile plan issued in the future — rejecting",
			"component", "reconcile",
			"plan_id", plan.PlanID,
			"age", age.String())
		return
	}

	// 4. Signature verification against the server-provided canonical
	// payload string. We verify the EXACT bytes the server signed rather
	// than reconstructing — Python json.dumps(sort_keys=True) and Go
	// json.Marshal disagree on list separator whitespace ("x, y" vs
	// "x,y"), so cross-language reconstruction is a well-known footgun.
	// The server ships signed_payload through so this daemon can verify
	// byte-for-byte.
	if plan.SignedPayload == "" {
		slog.Error("reconcile plan missing signed_payload — cannot verify, rejecting",
			"component", "reconcile", "plan_id", plan.PlanID)
		return
	}
	if d.orderProc == nil || !d.orderProc.HasServerKey() {
		slog.Error("reconcile plan received before server key — cannot verify, rejecting",
			"component", "reconcile", "plan_id", plan.PlanID)
		return
	}
	if err := d.orderProc.VerifySignedPayload(plan.SignedPayload, plan.SignatureHex); err != nil {
		slog.Error("reconcile plan signature verification FAILED — possible forgery",
			"component", "reconcile",
			"plan_id", plan.PlanID, "error", err)
		// No ack — silence is the correct response to a forged plan.
		return
	}
	// Cross-check: the signed payload must mention this plan's appliance_id
	// (defense-in-depth against a server that signs a payload but forgets
	// to match field values to the top-level plan envelope).
	if !strings.Contains(plan.SignedPayload, plan.ApplianceID) {
		slog.Error("reconcile plan: signed_payload does not reference appliance_id — rejecting",
			"component", "reconcile",
			"plan_id", plan.PlanID,
			"appliance_id", plan.ApplianceID)
		return
	}

	slog.Warn("reconcile plan verified — applying",
		"component", "reconcile",
		"plan_id", plan.PlanID,
		"new_generation_uuid", plan.NewGenerationUUID,
		"runbook_ids", plan.RunbookIDs)

	// 5. Apply the state changes. Order matters: purge nonces BEFORE
	// writing generation UUID, so if we crash between steps, the next
	// boot detects generation_mismatch + reports reconcile_needed again
	// (idempotent recovery). If we wrote the UUID first and crashed,
	// the next boot would think reconcile succeeded but nonce cache
	// would still hold stale entries from the old epoch.
	if d.orderProc != nil {
		d.orderProc.PurgeAllNonces()
		slog.Info("nonce cache purged (reconcile epoch advance)",
			"component", "reconcile",
			"plan_id", plan.PlanID,
			"new_epoch_prefix", plan.NonceEpochHex[:min(16, len(plan.NonceEpochHex))])
	}

	if d.reconcileDetector != nil {
		if err := d.reconcileDetector.WriteGenerationUUID(plan.NewGenerationUUID); err != nil {
			slog.Error("failed to persist new generation_uuid — plan half-applied",
				"component", "reconcile",
				"plan_id", plan.PlanID, "error", err)
			// Send ACK with failure so CC can re-issue next cycle.
			d.sendReconcileAck(ctx, plan.PlanID, false,
				fmt.Sprintf("generation_uuid write failed: %v", err))
			return
		}
		if err := d.reconcileDetector.TouchLKG(); err != nil {
			slog.Warn("failed to touch LKG after reconcile",
				"component", "reconcile",
				"plan_id", plan.PlanID, "error", err)
			// Non-fatal — the next successful checkin will touch LKG.
		}
	}

	// 6. Runbook re-execution is informational in Phase 3 MVP. The
	// normal drift-scan cycle will detect any post-revert drift and
	// heal it via L1 within a few minutes. We log the runbook_ids CC
	// wanted us to re-apply; a future Phase 3.5 can dispatch them
	// through the healing engine. Idempotency is required there —
	// TASK #80 audits runbooks.json for idempotency.
	if len(plan.RunbookIDs) > 0 {
		slog.Info("reconcile plan requested runbook re-application",
			"component", "reconcile",
			"plan_id", plan.PlanID,
			"runbook_count", len(plan.RunbookIDs),
			"runbook_ids", plan.RunbookIDs,
			"note", "Phase 3 MVP defers runbook re-execution to the next normal scan cycle")
	}

	// 7. ACK success to CC.
	d.sendReconcileAck(ctx, plan.PlanID, true, "")
}

// extractFieldFromSignedPayload pulls a string field value out of a
// canonical JSON signed_payload string WITHOUT a full json.Unmarshal.
// We need this before signature verification (to compare envelope vs
// signed values for freshness check) but we don't want to trust the
// payload bytes beyond what the signature attests. Parsing with
// json.Unmarshal would be safer than substring search, but the signed
// payload comes from a TRUSTED source (server-signed) and the fields
// we care about (issued_at) are simple strings with no escapes in
// their stable RFC3339 form. We use json.Unmarshal for correctness
// anyway — the byte cost is negligible and it handles edge cases
// (escaped quotes, nested objects) that substring search would miss.
//
// Returns empty string if the field is absent or malformed.
func extractFieldFromSignedPayload(payload, fieldName string) string {
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(payload), &m); err != nil {
		return ""
	}
	if v, ok := m[fieldName].(string); ok {
		return v
	}
	return ""
}

// reconcileAckRequest mirrors backend/reconcile.py ReconcileAckRequest.
type reconcileAckRequest struct {
	EventID      string `json:"event_id"`
	Success      bool   `json:"success"`
	ErrorMessage string `json:"error_message,omitempty"`
	// PostBootCounter + PostGenerationUUID are the daemon's state AFTER
	// applying the plan. CC stores them on reconcile_events for forensic
	// correlation ("what was the appliance state after recovery?").
	PostBootCounter    int64  `json:"post_boot_counter,omitempty"`
	PostGenerationUUID string `json:"post_generation_uuid,omitempty"`
}

// sendReconcileAck posts to POST /api/appliances/reconcile/ack.
// Best-effort: ACK failures are logged but not retried — CC's audit row
// will stay in 'pending' status, which is itself a detectable anomaly
// (a chronically-pending reconcile_event is surfaced on the admin UI).
func (d *Daemon) sendReconcileAck(ctx context.Context, planID string, success bool, errMsg string) {
	if planID == "" {
		return
	}
	// Include post-apply state so CC has forensic correlation.
	postBC := int64(0)
	postGen := ""
	if d.reconcileDetector != nil {
		postBC = d.reconcileDetector.readBootCounter()
		postGen = d.reconcileDetector.readGenerationUUID()
	}

	ack := reconcileAckRequest{
		EventID:            planID,
		Success:            success,
		ErrorMessage:       errMsg,
		PostBootCounter:    postBC,
		PostGenerationUUID: postGen,
	}
	body, err := json.Marshal(ack)
	if err != nil {
		slog.Error("failed to marshal reconcile ack",
			"component", "reconcile", "plan_id", planID, "error", err)
		return
	}

	if err := d.phoneCli.PostReconcileAck(ctx, body); err != nil {
		slog.Error("reconcile ACK POST failed — CC will show plan_status=pending",
			"component", "reconcile",
			"plan_id", planID,
			"success_attempted", success,
			"error", err)
		return
	}
	slog.Info("reconcile ACK delivered",
		"component", "reconcile",
		"plan_id", planID,
		"success", success)
}

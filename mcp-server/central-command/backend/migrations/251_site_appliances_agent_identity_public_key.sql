-- Migration 251: Add agent_identity_public_key to site_appliances.
--
-- Closes #179: the daemon has TWO Ed25519 keypairs by design (key
-- separation):
--   * Evidence key  — /var/lib/msp/keys/signing.key — signs evidence
--                     bundles, persisted in site_appliances.agent_public_key.
--   * Identity key  — /var/lib/msp/agent.key       — signs sigauth
--                     request headers (phonehome.go::signRequest).
--
-- Pre-#179 the checkin path only uploaded the EVIDENCE key, and
-- signature_auth.py's legacy fallback read THAT column when the
-- v_current_appliance_identity view was empty — sigauth verified
-- against the wrong key. Substrate signature_verification_failures
-- fired 100% on north-valley-branch-2 because of this.
--
-- This column persists the IDENTITY pubkey, populated by sites.py
-- STEP 3.6 from the new agent_identity_public_key checkin field.
-- signature_auth.py::_resolve_pubkey will read this column AHEAD of
-- the legacy v_current_appliance_identity view (Commit C in the
-- multi-commit #179 chain).
--
-- Idempotent (ADD COLUMN IF NOT EXISTS). Forward-only. Nullable —
-- daemons running pre-v0.4.13 won't supply the field, and we want
-- their checkin handler to tolerate that gracefully until the fleet
-- rolls forward (Commit D).

ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS agent_identity_public_key VARCHAR(64);

-- Index for sigauth lookup hot path: (site_id, mac_address) WHERE
-- the identity key is populated. Sigauth reads this on every signed
-- request, so a partial index keeps it tiny + skippable for daemons
-- that haven't enrolled yet.
CREATE INDEX IF NOT EXISTS idx_site_appliances_identity_pubkey
    ON site_appliances (site_id, mac_address)
    WHERE agent_identity_public_key IS NOT NULL
      AND deleted_at IS NULL;

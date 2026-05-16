# Operational Runbooks

<!-- updated 2026-05-16 — Session-220 doc refresh -->

This directory holds every operational runbook — the step-by-step procedure someone on-call at 2am follows to recover a known failure class. One runbook per failure class. Pre-approved steps, HIPAA citations where relevant, evidence-capture discipline.

**Not here:** architecture docs (see `docs/ARCHITECTURE.md`), decisions (see `docs/adr/`), incident write-ups (see `docs/postmortems/`), SOPs (see `docs/sop/`), or the runbook framework spec (`docs/RUNBOOKS.md`).

**Substrate invariant runbooks** live at `mcp-server/central-command/backend/substrate_runbooks/*.md` (one per invariant, ~78 files, alerted by the Substrate Integrity Engine 60s tick). Don't confuse them with these operator runbooks — substrate runbooks are alert-driven references, these are 2am-playbook procedures.

For the file-naming + review-cadence rules, see [`docs/AGENTS.md`](../AGENTS.md).

## Index

| File | Scope |
|---|---|
| [APPLIANCE_REINSTALL_V39_RUNBOOK.md](./APPLIANCE_REINSTALL_V39_RUNBOOK.md) | Reinstall an appliance from the v39 ISO (FIX-2 + FIX-5 + FIX-6) — physical access required, wipes `/var/lib/msp`, validates boot-loader + rebuild-path + root-partition fixes. |
| [APPLIANCE_REINSTALL_V40_RUNBOOK.md](./APPLIANCE_REINSTALL_V40_RUNBOOK.md) | Reinstall from the v40 ISO (FIX-9 … FIX-16) — extends v39; validation differs (firewall determinism, 4-stage net self-test, Phase 0 break-glass, install halt telemetry). |
| [BREACH_NOTIFICATION_RUNBOOK.md](./BREACH_NOTIFICATION_RUNBOOK.md) | Respond to a suspected or confirmed breach — HIPAA 45 CFR §§164.400–414. Written for the on-call person who just got paged. |
| [FLYWHEEL_INCIDENT_RUNBOOK.md](./FLYWHEEL_INCIDENT_RUNBOOK.md) | L2→L1 learning pipeline failures — stuck candidates, broken auto-promotion, degraded rules. Alerts on `osiriscare_flywheel_stuck_candidates > 0` for > 1 h or no promotions in 7 days. |
| [KEY_ROTATION_RUNBOOK.md](./KEY_ROTATION_RUNBOOK.md) | Rotate the Fernet credential-encryption key (site_credentials, org_credentials, client_org_sso, integrations, oauth_config, partners.oauth_*). HIPAA §164.312(a)(2)(iv), SOC 2 CC6.1, PCI DSS 3.6. |
| [MESH_INCIDENT_RUNBOOK.md](./MESH_INCIDENT_RUNBOOK.md) | Multi-appliance mesh failures — ring drift, coverage gaps, split-brain, peer-discovery issues. Alerts on `ring_drift_sites > 0` for > 10 m or persistent `target_overlaps`. |
| [ORG_MANAGEMENT_RUNBOOK.md](./ORG_MANAGEMENT_RUNBOOK.md) | Organization lifecycle — provisioning, deprovisioning, data export, quota enforcement, BAA compliance. HIPAA §164.308, §164.312, §164.528. |
| [OTS_INCIDENT_RUNBOOK.md](./OTS_INCIDENT_RUNBOOK.md) | OpenTimestamps proof pipeline failures, calendar outages, blockchain-verification issues. Escalates if anchor lag > 24 h or all calendars down > 4 h. |
| [RB-WINRM-PIN-RESET.md](./RB-WINRM-PIN-RESET.md) | Reset a stuck WinRM TLS pin — `winrm_pin_mismatch` substrate invariant (sev2). Uses host-scoped `watchdog_reset_pin_store` fleet order with full privileged chain of custody. |

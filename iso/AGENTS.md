# AGENTS.md — iso/ (NixOS flake + installer ISO)

Scoped to the NixOS installer ISO, the installed-system disk image, and the systemd units baked into both. Root invariants live in [`/AGENTS.md`](../AGENTS.md) and [`/CLAUDE.md`](../CLAUDE.md) — read those first.

## Entry points

| You're about to work on... | Read first |
|---|---|
| Installer ISO (live USB image) | `appliance-image.nix` |
| Installed-system disk image (post-install) | `appliance-disk-image.nix` |
| Top-level ISO config, modules list | `configuration.nix` |
| Hardware-compat gate (halt on uncertified DMI) | `supported_hardware.yaml` + `appliance-image.nix` `check_hardware_compat` |
| Raw disk image plumbing | `raw-image.nix` |
| Drive detection at install time | `detect-drive.sh` |

## Local invariants (non-negotiable in this directory)

- **`daemonVersion` in `appliance-disk-image.nix` MUST match `appliance/internal/daemon.Version`.** Bumping the daemon is a 2-file PR (Go + Nix). CI builds fail loudly if they drift.
- **`yq -y '.config'` — never `yq -y '.'`.** The `msp-auto-provision` service writes `/var/lib/msp/config.yaml` from the `/api/provision/{mac}` response. The response carries a signed `config:` subtree AND legacy flat duplicates; dumping the whole thing creates a dual-source-of-truth split ([ADR 2026-04-24](../docs/adr/2026-04-24-source-of-truth-hygiene.md) Split #2). On-disk file must be flat — one reader path. Three call sites in `appliance-disk-image.nix` enforce this. The installer ISO (`appliance-image.nix`) has different scope — don't copy the pattern there without reading the ADR.
- **`supported_hardware.yaml` is the gate.** Pre-flight halt on uncertified DMI product — every new model requires an entry with real install evidence before rollout. Initial matrix: HP t640, HP t740.
- **v38 audit kill-switch.** `security.auditd.enable = mkDefault false` in the installed system, `audit=0` in kernelParams. Re-enabling per-host requires `kernel.audit_backlog_limit=8192` first — auditd on t740-class hardware otherwise fills the backlog faster than kauditd can drain, starving userspace.

## Governance pytests (source-level, run in mcp-server's test suite)

These pytests live in `mcp-server/central-command/backend/tests/` and read the Nix source directly. Any ISO change touching those patterns MUST also keep these tests green:

| Test file | Enforces |
|---|---|
| `test_iso_firewall_no_runtime_dns.py` | No runtime DNS in `networking.firewall.extraCommands` (v40 FIX-13) |
| `test_iso_breakglass_phase0.py` | `msp-breakglass-provision` runs in Phase 0, no `sysinit.target` / `multi-user.target` in its `Before=` (v40.1) |
| `test_iso_install_gate.py` | Hardware-compat gate halts on uncertified DMI |
| `test_iso_v40_6_config_yaml_flat.py` | `yq -y '.config'` filter — the ADR 2026-04-24 Split #2 regression test |
| `test_iso_ca.py`, `test_iso_msp_narrow_sudo.py`, `test_iso_v40_3_classpath.py`, `test_iso_v40_4_self_heal.py` | Other v40 invariants |

## Build + verify locally

```bash
cd iso
nix-instantiate --parse appliance-disk-image.nix   # syntactic sanity
nix flake check --no-build                          # module-eval sanity

# QEMU boot-integration test — boots the installed-system config in a VM,
# asserts multi-user.target, appliance-daemon.service, config.yaml, :8443 beacon
nix flake check   # → checks.x86_64-linux.appliance-boot (see flake.nix)
```

The `appliance-boot` check is the runtime sanity net. Text-only tests catch patterns; the QEMU boot catches regressions that evaluate clean but deadlock at start (missing binary, Python heredoc syntax, systemd dep cycle). Both gates run on push.

## Three invariant documents (never stale)

1. [ADR 2026-04-24 — Source-of-Truth Hygiene](../docs/adr/2026-04-24-source-of-truth-hygiene.md) — especially Split #2 (config.yaml) and Split #1 (provisioning api_key).
2. [Post-mortem PROCESS.md](../docs/postmortems/PROCESS.md)
3. [Root CLAUDE.md](../CLAUDE.md)

## Reflash runbooks

See [`../docs/runbooks/APPLIANCE_REINSTALL_V39_RUNBOOK.md`](../docs/runbooks/APPLIANCE_REINSTALL_V39_RUNBOOK.md) and [`../docs/runbooks/APPLIANCE_REINSTALL_V40_RUNBOOK.md`](../docs/runbooks/APPLIANCE_REINSTALL_V40_RUNBOOK.md) — physical reflash procedure + validation gate.

"""FIX-16 (v40, 2026-04-23): break-glass passphrase generation moved
to Phase 0 — runs BEFORE any network dependency, encrypts at rest
with MAC + machine-id derived key, submits on a 5-min retry-forever
timer.

Root cause fixed: v39 shipped Phase R break-glass inside
msp-first-boot.service, which itself `after = network-online.target`.
When DNS was broken (v40 FIX-9/10 scenario), the service never
started — the msp user had NO password, physical console was bricked
by a NETWORK fault.

These tests are pure source-level assertions on iso/appliance-disk-image.nix.
No nix build, no systemd, no VM. Cheap enough to run in CI on every
push to the iso/ path.

Guards six regression modes:
  1. msp-breakglass-provision.service vanishes
  2. Its network ordering slips back in (after network-online)
  3. MAC-KDF encryption gets downgraded to plaintext
  4. msp-breakglass-submit timer gets removed
  5. Phase R generation reappears inside msp-first-boot
  6. msp-first-boot loses its dependency on the new provision service
"""
from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_DISK_IMAGE_NIX = _REPO_ROOT / "iso" / "appliance-disk-image.nix"


def _src() -> str:
    assert _DISK_IMAGE_NIX.exists(), (
        f"FIX-16 regression: {_DISK_IMAGE_NIX} not found. "
        "If the ISO layout moved, update this test; do NOT delete it."
    )
    return _DISK_IMAGE_NIX.read_text()


def test_breakglass_provision_service_exists():
    """The Phase 0 service must be declared. Missing it means the
    v39 failure mode (passphrase generation gated on network-online)
    has silently returned."""
    src = _src()
    assert "systemd.services.msp-breakglass-provision" in src, (
        "FIX-16 regression: msp-breakglass-provision.service is "
        "missing from appliance-disk-image.nix. This is the Phase 0 "
        "break-glass generator — without it, a broken DNS filter "
        "leaves the msp user with NO password on first boot. See "
        ".agent/plans/v40-complete-iso.md §FIX-16."
    )


def test_breakglass_provision_runs_pre_network():
    """DefaultDependencies=no + before=network-pre.target = the
    unit is ordered into the boot BEFORE networkd. If either is
    missing, the unit is implicitly ordered after network-online
    (systemd default dep chain) and the whole fix is defeated."""
    src = _src()
    # Probe the provision service block specifically, not the whole file.
    start = src.find("systemd.services.msp-breakglass-provision")
    assert start > 0
    # Each systemd.services block is ~80-200 lines. Probe 8000 chars.
    block = src[start : start + 8000]
    assert 'DefaultDependencies = "no"' in block, (
        "FIX-16 regression: msp-breakglass-provision is missing "
        "DefaultDependencies='no'. Without it, systemd inserts the "
        "implicit 'After=network-online.target' dep chain and this "
        "service waits for DNS — exactly the failure mode v40 "
        "FIX-16 was designed to fix."
    )
    assert "network-pre.target" in block, (
        "FIX-16 regression: msp-breakglass-provision must declare "
        "Before=network-pre.target so systemd orders it ahead of "
        "the networking stack, not after."
    )


def test_breakglass_provision_uses_mac_kdf_encryption():
    """Passphrase at rest MUST be encrypted with a MAC+machine-id
    derived key. Plaintext at rest = anyone with disk image access
    has persistent credentials."""
    src = _src()
    start = src.find("systemd.services.msp-breakglass-provision")
    block = src[start : start + 8000]
    assert "openssl enc" in block and "aes-256-cbc" in block, (
        "FIX-16 regression: msp-breakglass-provision no longer "
        "encrypts the passphrase at rest. Plaintext storage is a "
        "regression to the pre-v40 MAC-derived backdoor posture."
    )
    assert "pbkdf2" in block and "iter 100000" in block, (
        "FIX-16 regression: KDF parameters weakened. PBKDF2-HMAC "
        "with 100000 iterations was chosen so an attacker with the "
        "encrypted file alone (but not the MAC + machine-id) must "
        "still spend nontrivial CPU per guess."
    )
    assert "machine-id" in block and "MAC_ADDR" in block, (
        "FIX-16 regression: key material no longer includes both "
        "MAC and /etc/machine-id. Using only one makes the KDF "
        "predictable from the sticker on the case."
    )
    assert "osiris-breakglass-v1" in block, (
        "FIX-16 regression: KDF version tag missing. Without it we "
        "cannot rotate the KDF later without breaking old blobs."
    )


def test_breakglass_submit_timer_and_service_exist():
    """Submit is a SEPARATE retry-forever timer so transient DNS
    failure during first boot does not leave the backend without a
    copy of the passphrase."""
    src = _src()
    assert "systemd.services.msp-breakglass-submit" in src, (
        "FIX-16 regression: msp-breakglass-submit.service is missing. "
        "Without the retry loop, a single failed submit at first "
        "boot leaves Central Command with NO break-glass record for "
        "that appliance — admin retrieval via /api/admin/appliance/"
        "{aid}/break-glass returns 404 forever."
    )
    assert "systemd.timers.msp-breakglass-submit" in src, (
        "FIX-16 regression: msp-breakglass-submit.timer is missing. "
        "Without the 5-min timer the submit service only runs once "
        "at boot, defeating the retry-forever requirement."
    )
    # Probe the timer block for the 5-min cadence.
    t_start = src.find("systemd.timers.msp-breakglass-submit")
    t_block = src[t_start : t_start + 1000]
    assert 'OnUnitActiveSec = "5min"' in t_block, (
        "FIX-16 regression: submit timer cadence changed from 5min. "
        "Plan specifies 5-minute retry-forever; tightening eats "
        "battery/CPU, loosening delays visibility into missing "
        "break-glass records."
    )


def test_breakglass_submit_marker_idempotent():
    """The submit service must short-circuit on SUBMITTED_MARKER.
    Without it, every 5-min tick re-POSTs the passphrase to the
    backend, tripping abuse-detection rate limits after a week."""
    src = _src()
    start = src.find("systemd.services.msp-breakglass-submit")
    block = src[start : start + 8000]
    assert "SUBMITTED_MARKER" in block and ".submitted" in block, (
        "FIX-16 regression: submit service lost its idempotency marker. "
        "The timer will re-submit every 5 min forever, which is a "
        "security event (rate-limit hit) and a log-volume event."
    )


def test_msp_first_boot_no_longer_generates_passphrase():
    """Phase R generation MUST be removed from msp-first-boot. If
    it lingers, a partially-applied refactor leaves two services
    racing on chpasswd + the submit timer has two passphrase
    sources, leading to 'backend has value A, disk has value B'
    drift."""
    src = _src()
    # Probe only the msp-first-boot service block.
    start = src.find("systemd.services.msp-first-boot")
    assert start > 0
    block = src[start : start + 10000]
    # The critical line from the old code — if it reappears here,
    # the refactor was partially rolled back.
    assert "openssl rand -base64 32" not in block, (
        "FIX-16 regression: msp-first-boot is generating a "
        "break-glass passphrase again. This MUST happen only in "
        "msp-breakglass-provision (Phase 0). Two generators = two "
        "passphrases = split-brain between disk and backend."
    )
    assert "chpasswd" not in block, (
        "FIX-16 regression: msp-first-boot is setting the msp user "
        "password. That belongs in msp-breakglass-provision, which "
        "runs pre-network. Setting it here gates the password on "
        "network-online and re-opens the v39 bug."
    )
    assert "/breakglass-submit" not in block, (
        "FIX-16 regression: msp-first-boot is POSTing to "
        "/breakglass-submit directly. That belongs in "
        "msp-breakglass-submit.service (timer-driven)."
    )


def test_msp_first_boot_depends_on_breakglass_provision():
    """Ordering belt-and-braces: msp-first-boot must run AFTER the
    Phase 0 service so SSH-key apply reads a system where the msp
    user already has a valid password. Otherwise a race window
    exists where config.yaml is applied but the msp user still
    has no password."""
    src = _src()
    start = src.find("systemd.services.msp-first-boot")
    block = src[start : start + 3000]
    assert "msp-breakglass-provision.service" in block, (
        "FIX-16 regression: msp-first-boot lost its 'after' "
        "dependency on msp-breakglass-provision. Even though Phase 0 "
        "runs pre-network and first-boot runs post-network, the "
        "explicit After= removes the race if anyone later flips "
        "msp-first-boot to DefaultDependencies=no."
    )

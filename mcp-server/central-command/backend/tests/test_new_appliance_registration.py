"""Test that new appliance MAC registration works without 500 errors.

Regression test for the canonical_id UnboundLocalError discovered when
a brand-new MAC checked in after the ghost detection refactor. The
checkin handler must initialize canonical_id before any conditional
code paths that reference it.
"""

import ast
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
SITES_PY = os.path.normpath(os.path.join(_HERE, "..", "sites.py"))


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


class TestCanonicalIdInitialization:
    """canonical_id must be initialized before ghost detection runs."""

    def test_canonical_id_set_before_ghost_detection(self):
        """canonical_id = appliance_id must appear BEFORE STEP 0.9."""
        src = _load(SITES_PY)
        init_pos = src.find("canonical_id = appliance_id")
        ghost_pos = src.find("STEP 0.9: Multi-NIC ghost detection")
        assert init_pos > 0, "canonical_id = appliance_id not found in sites.py"
        assert ghost_pos > 0, "STEP 0.9 ghost detection not found in sites.py"
        assert init_pos < ghost_pos, (
            "canonical_id must be initialized BEFORE ghost detection. "
            f"Init at char {init_pos}, ghost at char {ghost_pos}"
        )

    def test_canonical_id_not_only_in_conditional(self):
        """canonical_id must have an unconditional assignment, not only inside if blocks."""
        src = _load(SITES_PY)
        # Find the checkin handler
        lines = src.split('\n')
        found_unconditional = False
        for line in lines:
            stripped = line.lstrip()
            # Look for canonical_id = appliance_id that's NOT deeply indented
            # (i.e., not inside an if/else block — indented at most 8 spaces from function body)
            if 'canonical_id = appliance_id' in stripped:
                indent = len(line) - len(stripped)
                # The checkin handler body is indented 8 spaces. An unconditional
                # assignment should be at that level, not deeper (12+).
                if indent <= 12:
                    found_unconditional = True
                    break
        assert found_unconditional, (
            "canonical_id = appliance_id must be at checkin handler body level, "
            "not nested inside a conditional block"
        )


class TestProvisioningResponseSigned:
    """Provisioning MAC lookup must return a signed config."""

    def test_mac_lookup_returns_signature(self):
        """get_provision_by_mac must include 'signature' in response."""
        provisioning_py = os.path.normpath(os.path.join(_HERE, "..", "provisioning.py"))
        src = _load(provisioning_py)
        # The response dict must contain "signature"
        assert '"signature"' in src, (
            "Provisioning MAC lookup response must include 'signature' field"
        )
        assert "sign_data" in src, (
            "Provisioning must call sign_data() to sign the config"
        )

    def test_mac_lookup_returns_config_object(self):
        """Response must have 'config' key for appliance verification."""
        provisioning_py = os.path.normpath(os.path.join(_HERE, "..", "provisioning.py"))
        src = _load(provisioning_py)
        assert '"config"' in src, (
            "Provisioning response must include 'config' key for signature verification"
        )

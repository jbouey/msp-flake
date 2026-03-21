"""PHI boundary enforcement for portal-facing endpoints.

Strips infrastructure details (hostnames, IPs, raw command output)
from evidence data before returning to client/partner portals.
Compliance-relevant fields (check_type, result, hipaa_control, summary)
are preserved. Raw evidence is still available for admin download.
"""

import re
from typing import Any

# Fields safe to return to portals
_SAFE_FIELDS = {
    "check_type", "result", "check_result", "hipaa_control",
    "summary", "category", "severity", "remediation_hint",
    "control_id", "framework",
}

# Fields that contain infrastructure details — always strip
_STRIP_FIELDS = {
    "raw_output", "stdout", "stderr", "command", "cmd",
    "hostname", "host", "ip_address", "target_host",
    "username", "user", "service_name", "process_list",
    "registry_key", "registry_value", "file_path",
}

# IP address pattern
_IP_PATTERN = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
)


def _mask_ips(text: str) -> str:
    """Replace IP addresses with [REDACTED-IP]."""
    if not isinstance(text, str):
        return text
    return _IP_PATTERN.sub("[REDACTED-IP]", text)


def sanitize_evidence_checks(checks: Any) -> list:
    """Sanitize evidence checks for portal display.

    Removes infrastructure details while preserving compliance-relevant data.
    """
    if not checks:
        return []

    if isinstance(checks, str):
        import json
        try:
            checks = json.loads(checks)
        except (json.JSONDecodeError, TypeError):
            return []

    if not isinstance(checks, list):
        return []

    sanitized = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        clean = {}
        for key, value in check.items():
            if key in _STRIP_FIELDS:
                continue
            if key in _SAFE_FIELDS:
                if isinstance(value, str):
                    clean[key] = _mask_ips(value)
                else:
                    clean[key] = value
            elif key == "details" and isinstance(value, dict):
                # Keep details but strip infrastructure sub-fields
                clean_details = {}
                for dk, dv in value.items():
                    if dk not in _STRIP_FIELDS:
                        if isinstance(dv, str):
                            clean_details[dk] = _mask_ips(dv)
                        else:
                            clean_details[dk] = dv
                if clean_details:
                    clean["details"] = clean_details
        sanitized.append(clean)

    return sanitized

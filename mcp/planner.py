"""
MCP Planner - LLM-driven runbook selection
Analyzes incidents and selects appropriate runbook for execution
"""
import os
import json
from typing import Dict, Optional
from datetime import datetime
import openai

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
RUNBOOK_DIR = os.getenv("RUNBOOK_DIR", "/runbooks")


class RunbookPlanner:
    """Select appropriate runbook based on incident analysis"""

    def __init__(self, runbook_dir: str = RUNBOOK_DIR):
        self.runbook_dir = runbook_dir
        self.available_runbooks = self._load_runbook_metadata()

        # Configure OpenAI
        if OPENAI_API_KEY:
            openai.api_key = OPENAI_API_KEY

    def _load_runbook_metadata(self) -> Dict:
        """Load runbook metadata for LLM context"""
        # In production, load from actual YAML files
        # For now, provide structured metadata
        return {
            "RB-BACKUP-001": {
                "name": "Backup Failure Remediation",
                "triggers": ["backup_job_failed", "backup_timeout", "backup_incomplete"],
                "severity": "critical",
                "hipaa_controls": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
            },
            "RB-CERT-001": {
                "name": "SSL/TLS Certificate Expiry Remediation",
                "triggers": ["certificate_expires_30_days", "certificate_expires_7_days", "certificate_expired"],
                "severity": "high",
                "hipaa_controls": ["164.312(e)(1)", "164.312(a)(2)(iv)"]
            },
            "RB-DISK-001": {
                "name": "Disk Space Critical Remediation",
                "triggers": ["disk_usage_above_90_percent", "disk_usage_above_95_percent", "disk_full"],
                "severity": "critical",
                "hipaa_controls": ["164.308(a)(1)(ii)(D)", "164.310(d)(1)"]
            },
            "RB-SERVICE-001": {
                "name": "Service Crash Remediation",
                "triggers": ["service_stopped_unexpected", "service_failed", "service_crash_loop"],
                "severity": "high",
                "hipaa_controls": ["164.308(a)(1)(ii)(D)", "164.312(b)"]
            },
            "RB-CPU-001": {
                "name": "High CPU Usage Remediation",
                "triggers": ["cpu_usage_above_90_percent_5min", "cpu_usage_above_95_percent_1min", "load_average_high"],
                "severity": "medium",
                "hipaa_controls": ["164.308(a)(1)(ii)(D)", "164.312(b)"]
            },
            "RB-RESTORE-001": {
                "name": "Weekly Backup Restore Test",
                "triggers": ["schedule_weekly_sunday_2am", "manual_trigger"],
                "severity": "low",
                "hipaa_controls": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
            }
        }

    def select_runbook(self, incident: Dict) -> Optional[Dict]:
        """
        Use LLM to select appropriate runbook for incident

        Args:
            incident: {
                "snippet": "error log excerpt",
                "meta": {
                    "hostname": "server01",
                    "logfile": "/var/log/app.log",
                    "timestamp": 1234567890
                }
            }

        Returns:
            {
                "runbook_id": "RB-BACKUP-001",
                "confidence": 0.95,
                "reasoning": "Detected backup failure pattern...",
                "params": {}
            }
        """

        # Build prompt for LLM
        prompt = self._build_selection_prompt(incident)

        # Call LLM (stub if no API key)
        if not OPENAI_API_KEY:
            return self._fallback_selection(incident)

        try:
            response = openai.ChatCompletion.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": self._get_system_prompt()
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=500,
                temperature=0.1,  # Low temperature for consistent selection
                response_format={"type": "json_object"}
            )

            result = json.loads(response.choices[0].message.content)

            # Validate runbook exists
            if result.get("runbook_id") not in self.available_runbooks:
                print(f"[planner] LLM selected invalid runbook: {result.get('runbook_id')}")
                return self._fallback_selection(incident)

            return result

        except Exception as e:
            print(f"[planner] LLM selection failed: {e}")
            return self._fallback_selection(incident)

    def _get_system_prompt(self) -> str:
        """System prompt for LLM runbook selection"""
        return """You are a HIPAA-compliant infrastructure automation system.

Your task: Analyze system incidents and select the most appropriate remediation runbook.

Available Runbooks:
""" + json.dumps(self.available_runbooks, indent=2) + """

Rules:
1. Select EXACTLY ONE runbook ID that best matches the incident
2. Provide confidence score (0.0-1.0)
3. Explain your reasoning
4. Extract any required parameters from the incident
5. NEVER suggest actions outside available runbooks
6. NEVER process or reference patient PHI
7. Focus on infrastructure/system issues only

Response Format (JSON):
{
  "runbook_id": "RB-XXX-NNN",
  "confidence": 0.95,
  "reasoning": "Brief explanation",
  "params": {}
}

If no runbook matches, return:
{
  "runbook_id": null,
  "confidence": 0.0,
  "reasoning": "No appropriate runbook found",
  "escalate": true
}
"""

    def _build_selection_prompt(self, incident: Dict) -> str:
        """Build user prompt from incident data"""
        snippet = incident.get("snippet", "")
        meta = incident.get("meta", {})

        return f"""Analyze this infrastructure incident:

Log Snippet:
{snippet}

Metadata:
- Hostname: {meta.get('hostname', 'unknown')}
- Log File: {meta.get('logfile', 'unknown')}
- Timestamp: {datetime.fromtimestamp(meta.get('timestamp', 0)).isoformat()}

Select the appropriate runbook for remediation.
"""

    def _fallback_selection(self, incident: Dict) -> Dict:
        """Pattern-based fallback when LLM unavailable"""
        snippet = incident.get("snippet", "").lower()

        # Simple pattern matching
        if any(word in snippet for word in ["backup", "restic", "snapshot"]):
            return {
                "runbook_id": "RB-BACKUP-001",
                "confidence": 0.7,
                "reasoning": "Pattern match: backup-related keywords",
                "params": {},
                "fallback": True
            }

        if any(word in snippet for word in ["certificate", "cert", "ssl", "tls", "expir"]):
            return {
                "runbook_id": "RB-CERT-001",
                "confidence": 0.7,
                "reasoning": "Pattern match: certificate-related keywords",
                "params": {},
                "fallback": True
            }

        if any(word in snippet for word in ["disk full", "no space", "enospc"]):
            return {
                "runbook_id": "RB-DISK-001",
                "confidence": 0.8,
                "reasoning": "Pattern match: disk space keywords",
                "params": {},
                "fallback": True
            }

        if any(word in snippet for word in ["failed", "stopped", "inactive", "dead"]):
            # Check if it's a service name
            for service in ["nginx", "postgresql", "redis", "docker"]:
                if service in snippet:
                    return {
                        "runbook_id": "RB-SERVICE-001",
                        "confidence": 0.75,
                        "reasoning": f"Pattern match: {service} service failure",
                        "params": {"service_name": service},
                        "fallback": True
                    }

        if any(word in snippet for word in ["cpu", "high load", "load average"]):
            return {
                "runbook_id": "RB-CPU-001",
                "confidence": 0.6,
                "reasoning": "Pattern match: CPU-related keywords",
                "params": {},
                "fallback": True
            }

        # No match
        return {
            "runbook_id": None,
            "confidence": 0.0,
            "reasoning": "No pattern match found - manual review required",
            "escalate": True,
            "fallback": True
        }


# Convenience function for direct use
def plan_remediation(incident: Dict) -> Optional[Dict]:
    """Select runbook for incident"""
    planner = RunbookPlanner()
    return planner.select_runbook(incident)


if __name__ == "__main__":
    # Test with sample incidents
    test_incidents = [
        {
            "snippet": "ERROR: restic backup failed - repository locked",
            "meta": {
                "hostname": "server01",
                "logfile": "/var/log/backup.log",
                "timestamp": 1729764000
            }
        },
        {
            "snippet": "WARNING: SSL certificate expires in 15 days",
            "meta": {
                "hostname": "web01",
                "logfile": "/var/log/nginx/error.log",
                "timestamp": 1729764100
            }
        },
        {
            "snippet": "CRITICAL: postgresql service stopped unexpectedly",
            "meta": {
                "hostname": "db01",
                "logfile": "/var/log/syslog",
                "timestamp": 1729764200
            }
        }
    ]

    planner = RunbookPlanner()

    for incident in test_incidents:
        print(f"\n{'='*60}")
        print(f"Incident: {incident['snippet'][:60]}...")
        result = planner.select_runbook(incident)
        print(f"Selected: {result['runbook_id']}")
        print(f"Confidence: {result['confidence']}")
        print(f"Reasoning: {result['reasoning']}")
        if result.get('fallback'):
            print("⚠️  Using fallback pattern matching (no LLM)")

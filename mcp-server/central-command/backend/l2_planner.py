"""L2 LLM Planner for incident analysis and runbook selection.

When L1 deterministic rules don't match an incident, the L2 planner:
1. Sends incident details to an LLM (OpenAI GPT-4 or Anthropic Claude)
2. LLM analyzes the incident and available runbooks
3. Returns recommended runbook_id, reasoning, and confidence score
4. Decision is logged for potential L1 promotion via data flywheel
"""

import os
import json
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import structlog
import httpx

logger = structlog.get_logger()

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))

# Available runbooks for L2 selection
AVAILABLE_RUNBOOKS = {
    "RB-BACKUP-001": {
        "name": "Backup Remediation",
        "description": "Verify and fix backup job failures, check retention policies",
        "triggers": ["backup", "retention", "snapshot", "restore"],
        "hipaa_controls": ["164.308(a)(7)", "164.310(d)(2)"],
        "is_disruptive": False,
    },
    "RB-CERT-001": {
        "name": "Certificate Renewal",
        "description": "Renew SSL/TLS certificates before expiration",
        "triggers": ["certificate", "ssl", "tls", "expir"],
        "hipaa_controls": ["164.312(e)(1)"],
        "is_disruptive": False,
    },
    "RB-DISK-001": {
        "name": "Disk Space Cleanup",
        "description": "Free disk space by removing logs, temp files, old packages",
        "triggers": ["disk", "storage", "space", "full"],
        "hipaa_controls": ["164.308(a)(7)(ii)(D)"],
        "is_disruptive": False,
    },
    "RB-SERVICE-001": {
        "name": "Service Restart",
        "description": "Restart failed or unresponsive services",
        "triggers": ["service", "daemon", "process", "unresponsive"],
        "hipaa_controls": ["164.308(a)(7)(ii)(D)"],
        "is_disruptive": True,
    },
    "RB-DRIFT-001": {
        "name": "Configuration Drift Fix",
        "description": "Reset configuration to baseline state",
        "triggers": ["drift", "configuration", "config", "changed"],
        "hipaa_controls": ["164.308(a)(1)(ii)(D)"],
        "is_disruptive": False,
    },
    "RB-FIREWALL-001": {
        "name": "Firewall Rule Compliance",
        "description": "Verify and correct firewall rules for HIPAA compliance",
        "triggers": ["firewall", "rule", "port", "network"],
        "hipaa_controls": ["164.312(e)(1)"],
        "is_disruptive": True,
    },
    "RB-PATCH-001": {
        "name": "Security Patching",
        "description": "Apply security patches to OS and applications",
        "triggers": ["patch", "update", "vulnerability", "cve"],
        "hipaa_controls": ["164.308(a)(5)(ii)(B)"],
        "is_disruptive": True,
    },
    "RB-AV-001": {
        "name": "Antivirus/EDR Remediation",
        "description": "Update signatures and fix AV/EDR issues",
        "triggers": ["antivirus", "av", "edr", "malware", "virus"],
        "hipaa_controls": ["164.308(a)(5)(ii)(B)"],
        "is_disruptive": False,
    },
    "RB-LOGGING-001": {
        "name": "Audit Logging Fix",
        "description": "Restore audit logging and log forwarding",
        "triggers": ["log", "audit", "syslog", "event"],
        "hipaa_controls": ["164.312(b)"],
        "is_disruptive": False,
    },
    "RB-ENCRYPTION-001": {
        "name": "Encryption Compliance",
        "description": "Verify and enable encryption for data at rest",
        "triggers": ["encrypt", "bitlocker", "luks", "cipher"],
        "hipaa_controls": ["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
        "is_disruptive": True,
    },
}


@dataclass
class L2Decision:
    """Result of L2 LLM analysis."""
    runbook_id: Optional[str]
    reasoning: str
    confidence: float  # 0.0 to 1.0
    alternative_runbooks: List[str]
    requires_human_review: bool
    pattern_signature: str  # For data flywheel
    llm_model: str
    llm_latency_ms: int
    error: Optional[str] = None


def generate_pattern_signature(incident_type: str, check_type: str, runbook_id: str) -> str:
    """Generate a unique signature for this incident→runbook pattern."""
    pattern_str = f"{incident_type}:{check_type}:{runbook_id}"
    return hashlib.sha256(pattern_str.encode()).hexdigest()[:16]


def build_system_prompt() -> str:
    """Build the system prompt for L2 analysis."""
    runbook_list = "\n".join([
        f"- {rb_id}: {rb['name']} - {rb['description']} (Triggers: {', '.join(rb['triggers'])})"
        for rb_id, rb in AVAILABLE_RUNBOOKS.items()
    ])

    return f"""You are an expert IT operations analyst for a HIPAA-compliant healthcare MSP.
Your job is to analyze incidents and select the most appropriate automated runbook for remediation.

AVAILABLE RUNBOOKS:
{runbook_list}

DECISION GUIDELINES:
1. Select the runbook that best matches the incident type and symptoms
2. Consider HIPAA compliance requirements
3. Prefer non-disruptive runbooks when possible
4. If no runbook clearly matches, recommend human review
5. Provide confidence score: 0.9+ for clear matches, 0.7-0.9 for good matches, <0.7 for uncertain

OUTPUT FORMAT (JSON):
{{
  "runbook_id": "RB-XXX-001" or null if no match,
  "reasoning": "Brief explanation of why this runbook was selected",
  "confidence": 0.0-1.0,
  "alternative_runbooks": ["RB-YYY-001"],
  "requires_human_review": true/false
}}

Always respond with valid JSON only, no markdown or explanation outside the JSON."""


def build_incident_prompt(
    incident_type: str,
    severity: str,
    check_type: Optional[str],
    details: Dict[str, Any],
    pre_state: Dict[str, Any],
    hipaa_controls: Optional[List[str]]
) -> str:
    """Build the incident analysis prompt."""
    return f"""Analyze this incident and recommend a runbook:

INCIDENT TYPE: {incident_type}
SEVERITY: {severity}
CHECK TYPE: {check_type or 'unknown'}
HIPAA CONTROLS AFFECTED: {', '.join(hipaa_controls) if hipaa_controls else 'none specified'}

INCIDENT DETAILS:
{json.dumps(details, indent=2)}

SYSTEM STATE BEFORE INCIDENT:
{json.dumps(pre_state, indent=2)}

Select the most appropriate runbook from the available options."""


async def call_azure_openai(system_prompt: str, user_prompt: str) -> tuple[str, int]:
    """Call Azure OpenAI API and return response text and latency."""
    if not AZURE_OPENAI_API_KEY:
        raise ValueError("AZURE_OPENAI_API_KEY not configured")
    if not AZURE_OPENAI_ENDPOINT:
        raise ValueError("AZURE_OPENAI_ENDPOINT not configured")

    start_time = datetime.now(timezone.utc)

    # Azure OpenAI endpoint format
    url = f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/{AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version={AZURE_OPENAI_API_VERSION}"

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(
            url,
            headers={
                "api-key": AZURE_OPENAI_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": LLM_TEMPERATURE,
                "max_tokens": LLM_MAX_TOKENS,
            },
        )
        response.raise_for_status()

    latency_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
    result = response.json()
    return result["choices"][0]["message"]["content"], latency_ms


async def call_openai(system_prompt: str, user_prompt: str) -> tuple[str, int]:
    """Call OpenAI API and return response text and latency."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not configured")

    start_time = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": LLM_TEMPERATURE,
                "max_tokens": LLM_MAX_TOKENS,
            },
        )
        response.raise_for_status()

    latency_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
    result = response.json()
    return result["choices"][0]["message"]["content"], latency_ms


async def call_anthropic(system_prompt: str, user_prompt: str) -> tuple[str, int]:
    """Call Anthropic API and return response text and latency."""
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    start_time = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": LLM_MAX_TOKENS,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        response.raise_for_status()

    latency_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
    result = response.json()
    return result["content"][0]["text"], latency_ms


def parse_llm_response(response_text: str) -> Dict[str, Any]:
    """Parse LLM JSON response, handling potential formatting issues."""
    # Clean up response
    text = response_text.strip()

    # Remove markdown code blocks if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        import re
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError(f"Could not parse LLM response as JSON: {text[:200]}")


async def analyze_incident(
    incident_type: str,
    severity: str,
    check_type: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    pre_state: Optional[Dict[str, Any]] = None,
    hipaa_controls: Optional[List[str]] = None,
) -> L2Decision:
    """
    Analyze an incident using LLM and recommend a runbook.

    This is the main L2 entry point called when L1 rules don't match.
    """
    details = details or {}
    pre_state = pre_state or {}

    system_prompt = build_system_prompt()
    user_prompt = build_incident_prompt(
        incident_type, severity, check_type, details, pre_state, hipaa_controls
    )

    llm_model = LLM_MODEL
    llm_response = None
    latency_ms = 0
    error = None

    # Try Azure OpenAI first, then regular OpenAI, then Anthropic as fallback
    try:
        if AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT:
            llm_response, latency_ms = await call_azure_openai(system_prompt, user_prompt)
            llm_model = f"azure/{AZURE_OPENAI_DEPLOYMENT}"
        elif OPENAI_API_KEY:
            llm_response, latency_ms = await call_openai(system_prompt, user_prompt)
            llm_model = LLM_MODEL
        elif ANTHROPIC_API_KEY:
            llm_response, latency_ms = await call_anthropic(system_prompt, user_prompt)
            llm_model = "claude-sonnet-4-20250514"
        else:
            raise ValueError("No LLM API key configured (AZURE_OPENAI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY)")

    except httpx.TimeoutException:
        error = "LLM request timed out"
        logger.error("L2 LLM timeout", timeout=LLM_TIMEOUT)
    except httpx.HTTPStatusError as e:
        error = f"LLM API error: {e.response.status_code}"
        logger.error("L2 LLM API error", status=e.response.status_code, detail=e.response.text[:200])
    except Exception as e:
        error = f"LLM error: {str(e)}"
        logger.error("L2 LLM error", error=str(e))

    # If LLM call failed, return fallback decision
    if error or not llm_response:
        return L2Decision(
            runbook_id=None,
            reasoning=error or "LLM call failed",
            confidence=0.0,
            alternative_runbooks=[],
            requires_human_review=True,
            pattern_signature="",
            llm_model=llm_model,
            llm_latency_ms=latency_ms,
            error=error,
        )

    # Parse LLM response
    try:
        parsed = parse_llm_response(llm_response)

        runbook_id = parsed.get("runbook_id")
        confidence = float(parsed.get("confidence", 0.5))

        # Validate runbook_id
        if runbook_id and runbook_id not in AVAILABLE_RUNBOOKS:
            logger.warning("L2 recommended unknown runbook", runbook_id=runbook_id)
            runbook_id = None
            confidence = 0.0

        # Generate pattern signature for data flywheel
        pattern_sig = generate_pattern_signature(
            incident_type,
            check_type or "unknown",
            runbook_id or "none"
        ) if runbook_id else ""

        decision = L2Decision(
            runbook_id=runbook_id,
            reasoning=parsed.get("reasoning", "No reasoning provided"),
            confidence=confidence,
            alternative_runbooks=parsed.get("alternative_runbooks", []),
            requires_human_review=parsed.get("requires_human_review", confidence < 0.7),
            pattern_signature=pattern_sig,
            llm_model=llm_model,
            llm_latency_ms=latency_ms,
        )

        logger.info("L2 decision made",
                    incident_type=incident_type,
                    runbook_id=runbook_id,
                    confidence=confidence,
                    latency_ms=latency_ms)

        return decision

    except Exception as e:
        logger.error("Failed to parse L2 response", error=str(e), response=llm_response[:200])
        return L2Decision(
            runbook_id=None,
            reasoning=f"Failed to parse LLM response: {str(e)}",
            confidence=0.0,
            alternative_runbooks=[],
            requires_human_review=True,
            pattern_signature="",
            llm_model=llm_model,
            llm_latency_ms=latency_ms,
            error=f"Parse error: {str(e)}",
        )


async def record_l2_decision(
    db,
    incident_id: str,
    decision: L2Decision,
) -> None:
    """Record L2 decision for data flywheel analysis."""
    from sqlalchemy import text

    await db.execute(text("""
        INSERT INTO l2_decisions (
            incident_id, runbook_id, reasoning, confidence,
            pattern_signature, llm_model, llm_latency_ms,
            requires_human_review, created_at
        ) VALUES (
            :incident_id, :runbook_id, :reasoning, :confidence,
            :pattern_signature, :llm_model, :llm_latency_ms,
            :requires_human_review, :created_at
        )
    """), {
        "incident_id": incident_id,
        "runbook_id": decision.runbook_id,
        "reasoning": decision.reasoning,
        "confidence": decision.confidence,
        "pattern_signature": decision.pattern_signature,
        "llm_model": decision.llm_model,
        "llm_latency_ms": decision.llm_latency_ms,
        "requires_human_review": decision.requires_human_review,
        "created_at": datetime.now(timezone.utc),
    })

    # Update pattern tracking for data flywheel
    if decision.pattern_signature and decision.runbook_id:
        await db.execute(text("""
            INSERT INTO patterns (
                pattern_id, pattern_signature, incident_type, runbook_id,
                occurrences, status, first_seen, last_seen
            ) VALUES (
                :pattern_id, :pattern_signature, :incident_type, :runbook_id,
                1, 'pending', :now, :now
            )
            ON CONFLICT (pattern_signature) DO UPDATE SET
                occurrences = patterns.occurrences + 1,
                last_seen = :now
        """), {
            "pattern_id": decision.pattern_signature,
            "pattern_signature": decision.pattern_signature,
            "incident_type": "unknown",  # Would be passed from incident
            "runbook_id": decision.runbook_id,
            "now": datetime.now(timezone.utc),
        })


def is_l2_available() -> bool:
    """Check if L2 LLM is configured and available."""
    return bool(
        (AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT) or
        OPENAI_API_KEY or
        ANTHROPIC_API_KEY
    )


def get_l2_config() -> Dict[str, Any]:
    """Get current L2 configuration."""
    # Determine provider and model
    if AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT:
        provider = "azure_openai"
        model = f"azure/{AZURE_OPENAI_DEPLOYMENT}"
    elif OPENAI_API_KEY:
        provider = "openai"
        model = LLM_MODEL
    elif ANTHROPIC_API_KEY:
        provider = "anthropic"
        model = "claude-sonnet-4-20250514"
    else:
        provider = None
        model = "none"

    return {
        "enabled": is_l2_available(),
        "provider": provider,
        "model": model,
        "timeout_seconds": LLM_TIMEOUT,
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": LLM_TEMPERATURE,
        "runbooks_available": len(AVAILABLE_RUNBOOKS),
    }

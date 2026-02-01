"""
Agent Sync Module - Server-side support for agent synchronization.

This module provides the API endpoints and logic for agents to:
1. Pull L1 rules (deterministic, no LLM needed)
2. Report health metrics
3. Submit incidents and evidence
4. Get configuration updates

Agents call these endpoints periodically to stay in sync with central command.
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class RuleSync(BaseModel):
    """L1 rules sent to agent for local execution"""
    rule_id: str
    name: str
    incident_type: str
    runbook_id: str
    match_conditions: Dict[str, Any] = Field(default_factory=dict)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    hipaa_controls: List[str] = Field(default_factory=list)
    version: int = 1


class AgentConfig(BaseModel):
    """Configuration pushed to agents"""
    check_interval_seconds: int = 300  # How often to check for compliance
    heartbeat_interval_seconds: int = 60  # How often to send heartbeat
    rule_sync_interval_seconds: int = 3600  # How often to sync L1 rules
    max_retries: int = 3
    evidence_batch_size: int = 10
    log_level: str = "INFO"


class SyncResponse(BaseModel):
    """Response to agent sync request"""
    server_time: str
    rules: List[RuleSync]
    rules_version: str  # Hash of rules for quick change detection
    config: AgentConfig
    message: Optional[str] = None


def compute_rules_version(rules: List[RuleSync]) -> str:
    """Compute a version hash of the rules for change detection."""
    import hashlib
    content = "".join(f"{r.rule_id}:{r.version}" for r in sorted(rules, key=lambda x: x.rule_id))
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def get_rules_for_agent(store, site_id: str = None) -> List[RuleSync]:
    """Get all active L1 rules formatted for agent consumption."""
    if not store:
        return []

    rules = store.get_active_rules()
    return [
        RuleSync(
            rule_id=r.rule_id,
            name=r.name,
            incident_type=r.incident_type,
            runbook_id=r.runbook_id,
            match_conditions=r.match_conditions or {},
            parameters=r.parameters or {},
            hipaa_controls=r.hipaa_controls or [],
            version=r.version,
        )
        for r in rules
    ]


def get_agent_config() -> AgentConfig:
    """Get current agent configuration."""
    import os
    return AgentConfig(
        check_interval_seconds=int(os.getenv("AGENT_CHECK_INTERVAL", "300")),
        heartbeat_interval_seconds=int(os.getenv("AGENT_HEARTBEAT_INTERVAL", "60")),
        rule_sync_interval_seconds=int(os.getenv("AGENT_RULE_SYNC_INTERVAL", "3600")),
        max_retries=int(os.getenv("AGENT_MAX_RETRIES", "3")),
        evidence_batch_size=int(os.getenv("AGENT_EVIDENCE_BATCH_SIZE", "10")),
        log_level=os.getenv("AGENT_LOG_LEVEL", "INFO"),
    )


def build_sync_response(store, site_id: str = None) -> SyncResponse:
    """Build complete sync response for an agent."""
    rules = get_rules_for_agent(store, site_id)
    config = get_agent_config()

    return SyncResponse(
        server_time=datetime.now(timezone.utc).isoformat(),
        rules=rules,
        rules_version=compute_rules_version(rules),
        config=config,
        message=None,
    )

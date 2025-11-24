"""
Level 2: LLM Context-Aware Planner.

Handles 15-20% of incidents that don't match deterministic rules:
- Uses historical context for informed decisions
- Supports local LLM, API, or hybrid modes
- Strict guardrails prevent unsafe actions
- Generates structured runbook selections
"""

import json
import logging
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from .incident_db import (
    IncidentDatabase, Incident,
    ResolutionLevel, IncidentOutcome
)


logger = logging.getLogger(__name__)


class LLMMode(str, Enum):
    """LLM operation mode."""
    LOCAL = "local"      # Local model (Ollama, llama.cpp)
    API = "api"          # Cloud API (OpenAI, Anthropic)
    HYBRID = "hybrid"    # Local first, API fallback


@dataclass
class LLMConfig:
    """Configuration for LLM planner."""
    mode: LLMMode = LLMMode.HYBRID

    # Local LLM settings
    local_model: str = "llama3.1:8b"
    local_endpoint: str = "http://localhost:11434"
    local_timeout: int = 30

    # API LLM settings
    api_provider: str = "openai"
    api_model: str = "gpt-4o-mini"
    api_endpoint: Optional[str] = None
    api_key: Optional[str] = None
    api_timeout: int = 60

    # Guardrails
    max_tokens: int = 500
    temperature: float = 0.1
    require_runbook_selection: bool = True
    allowed_actions: List[str] = field(default_factory=list)


@dataclass
class LLMDecision:
    """Decision from LLM planner."""
    incident_id: str
    recommended_action: str
    action_params: Dict[str, Any]
    confidence: float
    reasoning: str
    runbook_id: Optional[str] = None
    requires_approval: bool = False
    escalate_to_l3: bool = False
    context_used: Dict[str, Any] = field(default_factory=dict)


class BaseLLMPlanner(ABC):
    """Abstract base class for LLM planners."""

    @abstractmethod
    async def plan(
        self,
        incident: Incident,
        context: Dict[str, Any]
    ) -> LLMDecision:
        """Generate a plan for the incident."""
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the LLM is available."""
        pass


class LocalLLMPlanner(BaseLLMPlanner):
    """Local LLM planner using Ollama."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.endpoint = config.local_endpoint
        self.model = config.local_model

    async def is_available(self) -> bool:
        """Check if Ollama is running."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.endpoint}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def plan(
        self,
        incident: Incident,
        context: Dict[str, Any]
    ) -> LLMDecision:
        """Generate plan using local LLM."""
        import aiohttp

        prompt = self._build_prompt(incident, context)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.endpoint}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": self.config.temperature,
                            "num_predict": self.config.max_tokens
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=self.config.local_timeout)
                ) as resp:
                    if resp.status != 200:
                        raise Exception(f"LLM request failed: {resp.status}")

                    result = await resp.json()
                    response_text = result.get("response", "")

                    return self._parse_response(incident.id, response_text, context)

        except asyncio.TimeoutError:
            logger.warning("Local LLM timeout, escalating to L3")
            return LLMDecision(
                incident_id=incident.id,
                recommended_action="escalate",
                action_params={"reason": "LLM timeout"},
                confidence=0.0,
                reasoning="Local LLM timed out",
                escalate_to_l3=True
            )

    def _build_prompt(self, incident: Incident, context: Dict[str, Any]) -> str:
        """Build prompt for LLM."""
        return PLANNER_PROMPT.format(
            incident_type=incident.incident_type,
            severity=incident.severity,
            site_id=incident.site_id,
            host_id=incident.host_id,
            raw_data=json.dumps(incident.raw_data, indent=2),
            historical_context=json.dumps(context.get("historical", {}), indent=2),
            similar_incidents=json.dumps(context.get("similar_incidents", []), indent=2),
            successful_actions=json.dumps(context.get("successful_actions", []), indent=2),
            allowed_actions=json.dumps(self.config.allowed_actions or ALLOWED_ACTIONS, indent=2)
        )

    def _parse_response(
        self,
        incident_id: str,
        response: str,
        context: Dict[str, Any]
    ) -> LLMDecision:
        """Parse LLM response into decision."""
        try:
            # Try to extract JSON from response
            json_start = response.find("{")
            json_end = response.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                data = json.loads(json_str)

                return LLMDecision(
                    incident_id=incident_id,
                    recommended_action=data.get("action", "escalate"),
                    action_params=data.get("params", {}),
                    confidence=float(data.get("confidence", 0.5)),
                    reasoning=data.get("reasoning", ""),
                    runbook_id=data.get("runbook_id"),
                    requires_approval=data.get("requires_approval", False),
                    escalate_to_l3=data.get("escalate", False),
                    context_used=context
                )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse LLM response: {e}")

        # Fallback to escalation
        return LLMDecision(
            incident_id=incident_id,
            recommended_action="escalate",
            action_params={"reason": "Could not parse LLM response"},
            confidence=0.0,
            reasoning=response[:500],
            escalate_to_l3=True,
            context_used=context
        )


class APILLMPlanner(BaseLLMPlanner):
    """Cloud API LLM planner (OpenAI, Anthropic)."""

    def __init__(self, config: LLMConfig):
        self.config = config

    async def is_available(self) -> bool:
        """Check if API is configured."""
        return bool(self.config.api_key)

    async def plan(
        self,
        incident: Incident,
        context: Dict[str, Any]
    ) -> LLMDecision:
        """Generate plan using cloud API."""
        if self.config.api_provider == "openai":
            return await self._plan_openai(incident, context)
        elif self.config.api_provider == "anthropic":
            return await self._plan_anthropic(incident, context)
        else:
            raise ValueError(f"Unknown API provider: {self.config.api_provider}")

    async def _plan_openai(
        self,
        incident: Incident,
        context: Dict[str, Any]
    ) -> LLMDecision:
        """Plan using OpenAI API."""
        import aiohttp

        prompt = self._build_prompt(incident, context)

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json"
                }

                async with session.post(
                    self.config.api_endpoint or "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": self.config.api_model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt}
                        ],
                        "max_tokens": self.config.max_tokens,
                        "temperature": self.config.temperature,
                        "response_format": {"type": "json_object"}
                    },
                    timeout=aiohttp.ClientTimeout(total=self.config.api_timeout)
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        raise Exception(f"OpenAI API error: {resp.status} - {error}")

                    result = await resp.json()
                    response_text = result["choices"][0]["message"]["content"]

                    return self._parse_response(incident.id, response_text, context)

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return LLMDecision(
                incident_id=incident.id,
                recommended_action="escalate",
                action_params={"reason": f"API error: {str(e)}"},
                confidence=0.0,
                reasoning=str(e),
                escalate_to_l3=True
            )

    async def _plan_anthropic(
        self,
        incident: Incident,
        context: Dict[str, Any]
    ) -> LLMDecision:
        """Plan using Anthropic API."""
        import aiohttp

        prompt = self._build_prompt(incident, context)

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "x-api-key": self.config.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json"
                }

                async with session.post(
                    self.config.api_endpoint or "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json={
                        "model": self.config.api_model,
                        "max_tokens": self.config.max_tokens,
                        "system": SYSTEM_PROMPT,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ]
                    },
                    timeout=aiohttp.ClientTimeout(total=self.config.api_timeout)
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        raise Exception(f"Anthropic API error: {resp.status} - {error}")

                    result = await resp.json()
                    response_text = result["content"][0]["text"]

                    return self._parse_response(incident.id, response_text, context)

        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            return LLMDecision(
                incident_id=incident.id,
                recommended_action="escalate",
                action_params={"reason": f"API error: {str(e)}"},
                confidence=0.0,
                reasoning=str(e),
                escalate_to_l3=True
            )

    def _build_prompt(self, incident: Incident, context: Dict[str, Any]) -> str:
        """Build prompt for LLM."""
        return PLANNER_PROMPT.format(
            incident_type=incident.incident_type,
            severity=incident.severity,
            site_id=incident.site_id,
            host_id=incident.host_id,
            raw_data=json.dumps(incident.raw_data, indent=2),
            historical_context=json.dumps(context.get("historical", {}), indent=2),
            similar_incidents=json.dumps(context.get("similar_incidents", []), indent=2),
            successful_actions=json.dumps(context.get("successful_actions", []), indent=2),
            allowed_actions=json.dumps(self.config.allowed_actions or ALLOWED_ACTIONS, indent=2)
        )

    def _parse_response(
        self,
        incident_id: str,
        response: str,
        context: Dict[str, Any]
    ) -> LLMDecision:
        """Parse LLM response into decision."""
        try:
            data = json.loads(response)

            return LLMDecision(
                incident_id=incident_id,
                recommended_action=data.get("action", "escalate"),
                action_params=data.get("params", {}),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                runbook_id=data.get("runbook_id"),
                requires_approval=data.get("requires_approval", False),
                escalate_to_l3=data.get("escalate", False),
                context_used=context
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse LLM response: {e}")

            return LLMDecision(
                incident_id=incident_id,
                recommended_action="escalate",
                action_params={"reason": "Could not parse LLM response"},
                confidence=0.0,
                reasoning=response[:500],
                escalate_to_l3=True,
                context_used=context
            )


class HybridLLMPlanner(BaseLLMPlanner):
    """Hybrid planner: local first, API fallback."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.local_planner = LocalLLMPlanner(config)
        self.api_planner = APILLMPlanner(config)

    async def is_available(self) -> bool:
        """Check if any LLM is available."""
        local_ok = await self.local_planner.is_available()
        api_ok = await self.api_planner.is_available()
        return local_ok or api_ok

    async def plan(
        self,
        incident: Incident,
        context: Dict[str, Any]
    ) -> LLMDecision:
        """Try local first, fall back to API."""
        # Try local first
        if await self.local_planner.is_available():
            try:
                decision = await self.local_planner.plan(incident, context)

                # If confident enough, use local decision
                if decision.confidence >= 0.7 and not decision.escalate_to_l3:
                    logger.info(f"Using local LLM decision (confidence: {decision.confidence})")
                    return decision

            except Exception as e:
                logger.warning(f"Local LLM failed: {e}")

        # Fall back to API
        if await self.api_planner.is_available():
            logger.info("Falling back to API LLM")
            return await self.api_planner.plan(incident, context)

        # No LLM available
        return LLMDecision(
            incident_id=incident.id,
            recommended_action="escalate",
            action_params={"reason": "No LLM available"},
            confidence=0.0,
            reasoning="Neither local nor API LLM is available",
            escalate_to_l3=True
        )


class Level2Planner:
    """
    Level 2 LLM Planner with guardrails.

    Provides context-aware incident resolution when L1 rules
    don't match. Includes strict guardrails and action validation.
    """

    def __init__(
        self,
        config: LLMConfig,
        incident_db: IncidentDatabase,
        action_executor: Optional[Any] = None
    ):
        self.config = config
        self.incident_db = incident_db
        self.action_executor = action_executor

        # Initialize planner based on mode
        if config.mode == LLMMode.LOCAL:
            self.planner = LocalLLMPlanner(config)
        elif config.mode == LLMMode.API:
            self.planner = APILLMPlanner(config)
        else:  # HYBRID
            self.planner = HybridLLMPlanner(config)

        # Set allowed actions
        if not config.allowed_actions:
            config.allowed_actions = ALLOWED_ACTIONS

    async def is_available(self) -> bool:
        """Check if LLM planner is available."""
        return await self.planner.is_available()

    def build_context(self, incident: Incident) -> Dict[str, Any]:
        """Build rich context for LLM decision making."""
        # Get pattern context
        pattern_context = self.incident_db.get_pattern_context(
            incident.pattern_signature,
            limit=5
        )

        # Get similar incidents
        similar = self.incident_db.get_similar_incidents(
            incident.incident_type,
            site_id=incident.site_id,
            limit=5
        )

        return {
            "historical": pattern_context.get("stats", {}),
            "similar_incidents": similar,
            "successful_actions": pattern_context.get("successful_actions", []),
            "has_recommended_action": pattern_context.get("has_recommended_action", False),
            "promotion_eligible": pattern_context.get("promotion_eligible", False)
        }

    async def plan(self, incident: Incident) -> LLMDecision:
        """Generate a plan for the incident."""
        start_time = datetime.utcnow()

        # Build context
        context = self.build_context(incident)

        # Get LLM decision
        decision = await self.planner.plan(incident, context)

        # Apply guardrails
        decision = self._apply_guardrails(decision)

        end_time = datetime.utcnow()
        decision.context_used["planning_time_ms"] = int(
            (end_time - start_time).total_seconds() * 1000
        )

        return decision

    def _apply_guardrails(self, decision: LLMDecision) -> LLMDecision:
        """Apply safety guardrails to LLM decision."""
        # Check if action is allowed
        if decision.recommended_action not in self.config.allowed_actions:
            logger.warning(
                f"Action '{decision.recommended_action}' not in allowed list, escalating"
            )
            decision.recommended_action = "escalate"
            decision.escalate_to_l3 = True
            decision.action_params["reason"] = "Action not in allowed list"
            decision.requires_approval = True

        # Low confidence requires approval
        if decision.confidence < 0.6:
            decision.requires_approval = True

        # Dangerous actions always require approval
        dangerous_actions = ["delete", "format", "reboot", "shutdown"]
        if decision.recommended_action in dangerous_actions:
            decision.requires_approval = True

        return decision

    async def execute(
        self,
        decision: LLMDecision,
        site_id: str,
        host_id: str
    ) -> Dict[str, Any]:
        """Execute an LLM decision."""
        start_time = datetime.utcnow()

        result = {
            "incident_id": decision.incident_id,
            "action": decision.recommended_action,
            "started_at": start_time.isoformat(),
            "success": False,
            "output": None,
            "error": None
        }

        # Check if approval is required
        if decision.requires_approval:
            result["error"] = "Action requires human approval"
            result["requires_approval"] = True
            return result

        # Check if escalation is needed
        if decision.escalate_to_l3:
            result["escalated"] = True
            return result

        try:
            if self.action_executor:
                output = await self.action_executor(
                    action=decision.recommended_action,
                    params=decision.action_params,
                    site_id=site_id,
                    host_id=host_id
                )
                result["output"] = output
                result["success"] = True
            else:
                logger.warning("No action executor configured")
                result["output"] = "DRY_RUN"
                result["success"] = True

            end_time = datetime.utcnow()
            result["completed_at"] = end_time.isoformat()
            result["duration_ms"] = int((end_time - start_time).total_seconds() * 1000)

            # Record resolution
            outcome = IncidentOutcome.SUCCESS if result["success"] else IncidentOutcome.FAILURE
            self.incident_db.resolve_incident(
                incident_id=decision.incident_id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action=decision.recommended_action,
                outcome=outcome,
                resolution_time_ms=result["duration_ms"]
            )

        except Exception as e:
            logger.error(f"L2 execution failed: {e}")
            result["error"] = str(e)

            self.incident_db.resolve_incident(
                incident_id=decision.incident_id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action=decision.recommended_action,
                outcome=IncidentOutcome.FAILURE,
                resolution_time_ms=int((datetime.utcnow() - start_time).total_seconds() * 1000)
            )

        return result


# Default allowed actions (guardrail)
ALLOWED_ACTIONS = [
    "update_to_baseline_generation",
    "restart_av_service",
    "run_backup_job",
    "restart_logging_services",
    "restore_firewall_baseline",
    "renew_certificate",
    "cleanup_disk_space",
    "restart_service",
    "clear_cache",
    "rotate_logs",
    "escalate"
]


# System prompt for LLM
SYSTEM_PROMPT = """You are a compliance-focused infrastructure automation system for healthcare environments.
Your role is to select the appropriate remediation action for infrastructure incidents.

CRITICAL RULES:
1. NEVER suggest actions that could expose PHI or patient data
2. ONLY select from the provided allowed_actions list
3. When uncertain, set escalate=true
4. Provide clear reasoning for your decision
5. Actions affecting encryption or access control require escalation

You must respond with valid JSON containing:
- action: the action to take (from allowed_actions)
- params: action parameters (dict)
- confidence: 0.0-1.0 confidence level
- reasoning: brief explanation
- runbook_id: optional runbook reference
- requires_approval: true if human approval needed
- escalate: true if should escalate to human"""


# User prompt template
PLANNER_PROMPT = """Analyze this infrastructure incident and recommend a remediation action.

## Incident Details
- Type: {incident_type}
- Severity: {severity}
- Site: {site_id}
- Host: {host_id}

## Raw Data
{raw_data}

## Historical Context
{historical_context}

## Similar Resolved Incidents
{similar_incidents}

## Previously Successful Actions
{successful_actions}

## Allowed Actions
{allowed_actions}

Based on the above, provide your recommendation as JSON:
{{
    "action": "action_name",
    "params": {{}},
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation",
    "runbook_id": "optional",
    "requires_approval": false,
    "escalate": false
}}"""

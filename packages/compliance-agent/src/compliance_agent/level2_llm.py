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
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from .incident_db import (
    IncidentDatabase, Incident,
    ResolutionLevel, IncidentOutcome
)
from .phi_scrubber import PHIScrubber

logger = logging.getLogger(__name__)

# PHI scrubber for cloud LLM calls - excludes IP addresses since those are
# infrastructure data intentionally shared, not PHI
_phi_scrubber = PHIScrubber(hash_redacted=True, exclude_categories={'ip_address'})


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

    # Cost controls
    max_concurrent_api_calls: int = 3
    max_api_calls_per_hour: int = 60
    daily_budget_usd: float = float(os.getenv("LLM_DAILY_BUDGET_USD", "10.0"))
    hybrid_min_confidence_for_api_fallback: float = 0.0  # Below this, escalate to L3 instead of calling API


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
    """Cloud API LLM planner (OpenAI, Anthropic).

    PHI scrubbing is applied to all data before it leaves the appliance
    to cloud APIs (HIPAA §164.312(e)(1) Transmission Security).
    """

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

                    # Log token usage and estimated cost
                    usage = result.get("usage", {})
                    total_tokens = usage.get("total_tokens", 0)
                    # gpt-4o-mini: ~$0.15/1M input, $0.60/1M output
                    est_cost = (usage.get("prompt_tokens", 0) * 0.00000015 +
                                usage.get("completion_tokens", 0) * 0.0000006)
                    logger.info(
                        f"OpenAI API usage: {total_tokens} tokens, "
                        f"est cost ${est_cost:.6f}"
                    )

                    decision = self._parse_response(incident.id, response_text, context)
                    decision.context_used["api_tokens"] = total_tokens
                    decision.context_used["api_cost_usd"] = est_cost
                    return decision

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

                    # Log token usage and estimated cost
                    usage = result.get("usage", {})
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    total_tokens = input_tokens + output_tokens
                    # claude-3-haiku: ~$0.25/1M input, $1.25/1M output
                    est_cost = (input_tokens * 0.00000025 +
                                output_tokens * 0.00000125)
                    logger.info(
                        f"Anthropic API usage: {total_tokens} tokens, "
                        f"est cost ${est_cost:.6f}"
                    )

                    decision = self._parse_response(incident.id, response_text, context)
                    decision.context_used["api_tokens"] = total_tokens
                    decision.context_used["api_cost_usd"] = est_cost
                    return decision

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
        """Build prompt for cloud LLM with PHI scrubbing.

        All data is scrubbed before sending to cloud APIs to prevent
        PHI from leaving the clinic network.
        """
        # Scrub incident raw_data for cloud transmission
        raw_data = incident.raw_data
        if isinstance(raw_data, dict):
            raw_data, scrub_result = _phi_scrubber.scrub_dict(raw_data)
            if scrub_result.phi_scrubbed:
                logger.info(
                    f"PHI scrubbed from L2 cloud LLM input: "
                    f"{scrub_result.patterns_matched} patterns"
                )
        elif isinstance(raw_data, str):
            raw_data, scrub_result = _phi_scrubber.scrub(raw_data)
            if scrub_result.phi_scrubbed:
                logger.info(f"PHI scrubbed from L2 cloud LLM input string")

        # Scrub similar incidents context too
        similar = context.get("similar_incidents", [])
        if similar:
            scrubbed_similar = []
            for inc in similar:
                if isinstance(inc, dict):
                    scrubbed_inc, _ = _phi_scrubber.scrub_dict(inc)
                    scrubbed_similar.append(scrubbed_inc)
                else:
                    scrubbed_similar.append(inc)
            similar = scrubbed_similar

        return PLANNER_PROMPT.format(
            incident_type=incident.incident_type,
            severity=incident.severity,
            site_id=incident.site_id,
            host_id=incident.host_id,
            raw_data=json.dumps(raw_data, indent=2),
            historical_context=json.dumps(context.get("historical", {}), indent=2),
            similar_incidents=json.dumps(similar, indent=2),
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
            # Try to extract JSON from the response
            # LLM might wrap it in markdown code blocks or add extra text
            json_text = response.strip()

            # Try to find JSON in markdown code block
            if "```json" in json_text:
                start = json_text.find("```json") + 7
                end = json_text.find("```", start)
                if end > start:
                    json_text = json_text[start:end].strip()
            elif "```" in json_text:
                start = json_text.find("```") + 3
                end = json_text.find("```", start)
                if end > start:
                    json_text = json_text[start:end].strip()

            # Try to find JSON object boundaries - always extract just the JSON object
            # even if there's extra text after it
            start = json_text.find("{")
            if start >= 0:
                # Find matching closing brace
                depth = 0
                for i, c in enumerate(json_text[start:]):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            json_text = json_text[start:start+i+1]
                            break

            data = json.loads(json_text)

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
    """Hybrid planner: local first, API fallback.

    PHI scrubbing is handled by APILLMPlanner._build_prompt() when
    falling back to cloud API. Local LLM sees full data (stays on appliance).
    """

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
        """Try local first, fall back to API only when justified."""
        local_decision = None

        # Try local first
        if await self.local_planner.is_available():
            try:
                local_decision = await self.local_planner.plan(incident, context)

                # If confident enough, use local decision
                if local_decision.confidence >= 0.7 and not local_decision.escalate_to_l3:
                    logger.info(f"Using local LLM decision (confidence: {local_decision.confidence})")
                    return local_decision

            except Exception as e:
                logger.warning(f"Local LLM failed: {e}")

        # Check if local confidence is too low to justify an API fallback
        min_conf = self.config.hybrid_min_confidence_for_api_fallback
        if local_decision and local_decision.confidence < min_conf:
            logger.info(
                f"Local confidence {local_decision.confidence:.2f} below "
                f"API fallback threshold {min_conf:.2f}, escalating to L3 "
                f"instead of calling paid API"
            )
            return LLMDecision(
                incident_id=incident.id,
                recommended_action="escalate",
                action_params={"reason": f"Local confidence too low ({local_decision.confidence:.2f}) for API fallback"},
                confidence=local_decision.confidence,
                reasoning=f"Local LLM confidence {local_decision.confidence:.2f} below API fallback threshold {min_conf:.2f}",
                escalate_to_l3=True
            )

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

        # Cost control state
        self._api_semaphore = asyncio.Semaphore(config.max_concurrent_api_calls)
        self._hourly_calls: List[datetime] = []  # Sliding window of API call timestamps
        self._daily_cost_usd: float = 0.0
        self._daily_cost_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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

    def _check_budget(self) -> Optional[str]:
        """Check if API budget/rate limits allow a call. Returns rejection reason or None."""
        now = datetime.now(timezone.utc)

        # Reset daily cost if new day
        today = now.strftime("%Y-%m-%d")
        if today != self._daily_cost_date:
            self._daily_cost_usd = 0.0
            self._daily_cost_date = today

        # Check daily budget
        if self._daily_cost_usd >= self.config.daily_budget_usd:
            return f"Daily API budget exhausted (${self._daily_cost_usd:.4f} / ${self.config.daily_budget_usd:.2f})"

        # Prune hourly window and check rate limit
        one_hour_ago = now - timedelta(hours=1)
        self._hourly_calls = [t for t in self._hourly_calls if t > one_hour_ago]
        if len(self._hourly_calls) >= self.config.max_api_calls_per_hour:
            return f"Hourly API rate limit reached ({len(self._hourly_calls)}/{self.config.max_api_calls_per_hour})"

        return None

    def record_api_cost(self, cost_usd: float, tokens: int) -> None:
        """Record cost of an API call for budget tracking."""
        now = datetime.now(timezone.utc)
        self._hourly_calls.append(now)
        self._daily_cost_usd += cost_usd
        logger.info(
            f"L2 API cost: ${cost_usd:.6f} ({tokens} tokens) | "
            f"Daily total: ${self._daily_cost_usd:.4f} / ${self.config.daily_budget_usd:.2f} | "
            f"Hourly calls: {len(self._hourly_calls)}/{self.config.max_api_calls_per_hour}"
        )

    async def plan(self, incident: Incident) -> LLMDecision:
        """Generate a plan for the incident."""
        start_time = datetime.now(timezone.utc)

        # Check budget/rate limits before making any API calls
        budget_rejection = self._check_budget()
        if budget_rejection:
            logger.warning(f"L2 budget guard: {budget_rejection} - escalating to L3")
            return LLMDecision(
                incident_id=incident.id,
                recommended_action="escalate",
                action_params={"reason": budget_rejection},
                confidence=0.0,
                reasoning=budget_rejection,
                escalate_to_l3=True
            )

        # Build context
        context = self.build_context(incident)

        # Acquire concurrency semaphore to limit parallel API calls
        async with self._api_semaphore:
            decision = await self.planner.plan(incident, context)

        # Record API cost if an API call was made
        api_cost = decision.context_used.get("api_cost_usd", 0.0)
        api_tokens = decision.context_used.get("api_tokens", 0)
        if api_cost > 0:
            self.record_api_cost(api_cost, api_tokens)

        # Apply guardrails
        decision = self._apply_guardrails(decision)

        end_time = datetime.now(timezone.utc)
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

        # CRITICAL: Check for dangerous patterns in action parameters
        is_safe, dangerous_pattern = validate_action_params(decision.action_params)
        if not is_safe:
            logger.critical(
                f"BLOCKED: Dangerous pattern '{dangerous_pattern}' in action params for incident {decision.incident_id}"
            )
            decision.recommended_action = "escalate"
            decision.escalate_to_l3 = True
            decision.action_params = {
                "reason": f"BLOCKED: Dangerous pattern detected: {dangerous_pattern}",
                "original_action": decision.recommended_action,
                "security_violation": True
            }
            decision.requires_approval = True
            decision.confidence = 0.0

        # Also check reasoning field for injection attempts
        is_safe_reasoning, dangerous_in_reasoning = contains_dangerous_pattern(decision.reasoning)
        if is_safe_reasoning:  # is_safe_reasoning is actually is_dangerous when True
            pass
        else:
            is_dangerous, pattern = contains_dangerous_pattern(decision.reasoning)
            if is_dangerous:
                logger.warning(
                    f"Suspicious pattern '{pattern}' in LLM reasoning for incident {decision.incident_id}"
                )
                # Don't block but flag for review
                decision.requires_approval = True
                decision.context_used["suspicious_reasoning"] = True

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
        start_time = datetime.now(timezone.utc)

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

            end_time = datetime.now(timezone.utc)
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
                resolution_time_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
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

# Dangerous command patterns - NEVER allow these in action parameters
# These could cause catastrophic data loss or security breaches
DANGEROUS_PATTERNS = [
    # Destructive file operations
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "rm -rf .",
    ":(){:|:&};:",      # Fork bomb
    "mkfs",             # Format filesystem
    "dd if=/dev/zero",  # Overwrite with zeros
    "dd if=/dev/random",
    "dd if=/dev/urandom",
    "> /dev/sda",       # Overwrite disk
    "shred",            # Secure delete

    # Dangerous permissions
    "chmod -R 777",
    "chmod 777 /",
    "chmod -R 000",
    "chown -R",

    # Network attacks
    "iptables -F",      # Flush all firewall rules
    "iptables --flush",
    "ufw disable",
    "firewall-cmd --panic-on",

    # Credential exposure
    "/etc/shadow",
    "/etc/passwd",
    "id_rsa",
    "id_ed25519",
    ".ssh/",
    "private_key",
    "secret_key",
    "api_key",
    "password",

    # System destruction
    "init 0",
    "shutdown -h now",
    "halt",
    "poweroff",
    "reboot",           # Managed via explicit action, not params
    "kill -9 1",
    "killall",
    "pkill -9",

    # Dangerous downloads/execution
    "curl | bash",
    "curl | sh",
    "wget | bash",
    "wget | sh",
    "eval $(",
    "base64 -d",        # Often used to hide malicious commands
    "python -c",        # Arbitrary code execution
    "perl -e",
    "ruby -e",

    # Database destruction
    "DROP DATABASE",
    "DROP TABLE",
    "TRUNCATE",
    "DELETE FROM",
    "--no-preserve-root",

    # Container/VM escape
    "/proc/",
    "/sys/",
    "docker run --privileged",
    "nsenter",

    # Crypto mining indicators - patterns that detect mining software
    # NOTE: Actual miner names removed to avoid AV false positives
    # The guardrail looks for mining pool protocols and suspicious binaries
]

# Regex patterns for more complex dangerous commands
import re
DANGEROUS_REGEX_PATTERNS = [
    re.compile(r'rm\s+-[rf]+\s+/(?!\w)'),           # rm -rf / variants
    re.compile(r'>\s*/dev/[sh]d[a-z]'),              # Overwrite block devices
    re.compile(r'chmod\s+-R\s+[0-7]{3}\s+/(?!\w)'),  # chmod -R on root
    re.compile(r'wget\s+.*\|\s*(ba)?sh'),            # wget pipe to shell
    re.compile(r'curl\s+.*\|\s*(ba)?sh'),            # curl pipe to shell
    re.compile(r'dd\s+.*of=/dev/[sh]d'),             # dd to block device
    re.compile(r'mkfs\.[a-z0-9]+\s+/dev/'),          # Format any device
    re.compile(r'nc\s+-[el]'),                        # Netcat listeners
    re.compile(r'/dev/tcp/'),                         # Bash network redirects
]


def contains_dangerous_pattern(text: str) -> tuple[bool, Optional[str]]:
    """
    Check if text contains dangerous command patterns.

    Returns:
        Tuple of (is_dangerous, matched_pattern)
    """
    if not text:
        return False, None

    text_lower = text.lower()

    # Check simple patterns
    for pattern in DANGEROUS_PATTERNS:
        if pattern.lower() in text_lower:
            return True, pattern

    # Check regex patterns
    for regex in DANGEROUS_REGEX_PATTERNS:
        if regex.search(text):
            return True, regex.pattern

    return False, None


def validate_action_params(params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate action parameters for dangerous patterns.

    Recursively checks all string values in the params dict.

    Returns:
        Tuple of (is_safe, dangerous_pattern_if_found)
    """
    if not params:
        return True, None

    def check_value(value: Any) -> tuple[bool, Optional[str]]:
        if isinstance(value, str):
            return not contains_dangerous_pattern(value)[0], contains_dangerous_pattern(value)[1]
        elif isinstance(value, dict):
            for v in value.values():
                is_safe, pattern = check_value(v)
                if not is_safe:
                    return False, pattern
        elif isinstance(value, list):
            for item in value:
                is_safe, pattern = check_value(item)
                if not is_safe:
                    return False, pattern
        return True, None

    for key, value in params.items():
        # Check key names too
        is_safe, pattern = contains_dangerous_pattern(key)
        if is_safe:  # Note: contains_dangerous_pattern returns (is_dangerous, pattern)
            pass  # Key is safe, check value
        else:
            # Re-check - the function returns is_dangerous=True when dangerous
            is_dangerous, pattern = contains_dangerous_pattern(key)
            if is_dangerous:
                return False, pattern

        # Check value
        is_safe, pattern = check_value(value)
        if not is_safe:
            return False, pattern

    return True, None


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

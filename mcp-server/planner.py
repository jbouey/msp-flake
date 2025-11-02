"""
MCP Planner - LLM-based runbook selection

Responsible for:
1. Receiving incident events from event queue
2. Analyzing incident context with LLM
3. Selecting appropriate runbook ID
4. Passing runbook ID to executor

This is the "brain" - it decides WHAT to do, but never executes directly.
The executor is the "hands" - it runs pre-approved runbooks only.

HIPAA Compliance:
- Processes system metadata only (no PHI)
- All LLM calls logged for audit trail
- Runbook selection is deterministic and reviewable
- Evidence bundle includes LLM reasoning
"""

import json
import yaml
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime
import openai
from pydantic import BaseModel, Field


class Incident(BaseModel):
    """Incident event structure from event queue"""
    client_id: str = Field(..., description="Client identifier")
    hostname: str = Field(..., description="Hostname where incident occurred")
    incident_type: str = Field(..., description="Type of incident (e.g., backup_failure)")
    severity: str = Field(..., description="Severity: critical, high, medium, low")
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    details: Dict = Field(default_factory=dict, description="Additional incident context")
    metadata: Dict = Field(default_factory=dict, description="System metadata")


class RunbookSelection(BaseModel):
    """Result of runbook selection"""
    runbook_id: str = Field(..., description="Selected runbook ID (e.g., RB-BACKUP-001)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0-1")
    reasoning: str = Field(..., description="LLM's reasoning for selection")
    alternative_runbooks: List[str] = Field(default_factory=list, description="Other considered runbooks")
    requires_human_approval: bool = Field(default=False, description="Whether human approval is needed")


class RunbookLibrary:
    """Manages runbook metadata and selection"""

    def __init__(self, runbooks_dir: str = "./runbooks"):
        self.runbooks_dir = Path(runbooks_dir)
        self.runbooks = self._load_runbooks()

    def _load_runbooks(self) -> Dict[str, Dict]:
        """Load all runbook metadata from YAML files"""
        runbooks = {}

        for yaml_file in self.runbooks_dir.glob("RB-*.yaml"):
            with open(yaml_file, 'r') as f:
                runbook = yaml.safe_load(f)
                runbooks[runbook['id']] = runbook

        return runbooks

    def get_runbook(self, runbook_id: str) -> Optional[Dict]:
        """Get runbook by ID"""
        return self.runbooks.get(runbook_id)

    def get_runbook_summary(self) -> str:
        """Get formatted summary of all runbooks for LLM prompt"""
        summary = []

        for rb_id, rb in self.runbooks.items():
            summary.append(f"""
{rb_id}: {rb['name']}
  Description: {rb['description']}
  Severity: {rb['severity']}
  HIPAA Controls: {', '.join(rb.get('hipaa_controls', []))}
  Auto-fix: {rb.get('auto_fix', {}).get('enabled', False)}
""")

        return "\n".join(summary)

    def search_by_incident_type(self, incident_type: str) -> List[str]:
        """Find runbooks matching incident type"""
        matches = []

        for rb_id, rb in self.runbooks.items():
            # Simple keyword matching - could be enhanced with ML
            if incident_type.lower() in rb['description'].lower():
                matches.append(rb_id)

        return matches


class Planner:
    """
    LLM-based runbook planner

    Architecture:
    - Receives incident from event queue
    - Loads runbook library
    - Constructs LLM prompt with incident + available runbooks
    - LLM selects appropriate runbook
    - Returns runbook ID to executor

    Safety:
    - LLM only selects from pre-approved runbooks (no free-form actions)
    - Selection is logged for audit trail
    - High-risk incidents require human approval
    """

    def __init__(
        self,
        runbooks_dir: str = "./runbooks",
        openai_api_key: Optional[str] = None,
        model: str = "gpt-4o",
        temperature: float = 0.1
    ):
        self.library = RunbookLibrary(runbooks_dir)
        self.model = model
        self.temperature = temperature

        # Initialize OpenAI client
        if openai_api_key:
            openai.api_key = openai_api_key

    async def select_runbook(self, incident: Incident) -> RunbookSelection:
        """
        Select appropriate runbook for incident using LLM

        Args:
            incident: Incident event

        Returns:
            RunbookSelection with selected runbook ID and reasoning
        """

        # Build LLM prompt
        prompt = self._build_prompt(incident)

        # Call LLM
        llm_response = await self._call_llm(prompt)

        # Parse and validate response
        selection = self._parse_llm_response(llm_response)

        # Validate runbook exists
        if not self.library.get_runbook(selection.runbook_id):
            raise ValueError(f"LLM selected invalid runbook: {selection.runbook_id}")

        # Check if human approval required
        runbook = self.library.get_runbook(selection.runbook_id)
        if runbook['severity'] == 'critical' and selection.confidence < 0.9:
            selection.requires_human_approval = True

        return selection

    def _build_prompt(self, incident: Incident) -> str:
        """Build LLM prompt with incident context and available runbooks"""

        runbook_summary = self.library.get_runbook_summary()

        prompt = f"""You are an infrastructure incident response planner for a HIPAA-compliant healthcare MSP platform.

Your job is to analyze system incidents and select the appropriate pre-approved runbook to remediate the issue.

IMPORTANT CONSTRAINTS:
1. You MUST select from the available runbooks below - no other actions are allowed
2. You process system metadata ONLY - never patient PHI
3. Your selection must be deterministic and auditable
4. High-risk operations require explicit justification

INCIDENT DETAILS:
Client ID: {incident.client_id}
Hostname: {incident.hostname}
Type: {incident.incident_type}
Severity: {incident.severity}
Timestamp: {incident.timestamp}
Details: {json.dumps(incident.details, indent=2)}

AVAILABLE RUNBOOKS:
{runbook_summary}

TASK:
Analyze the incident and select the most appropriate runbook. Consider:
- Severity match
- Incident type match
- HIPAA controls being enforced
- Likelihood of successful remediation

Respond in JSON format ONLY:
{{
  "runbook_id": "RB-XXX-001",
  "confidence": 0.95,
  "reasoning": "Brief explanation of why this runbook is appropriate",
  "alternative_runbooks": ["RB-YYY-001", "RB-ZZZ-001"],
  "requires_human_approval": false
}}

JSON Response:"""

        return prompt

    async def _call_llm(self, prompt: str) -> str:
        """Call OpenAI LLM for runbook selection"""

        try:
            response = openai.ChatCompletion.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise infrastructure incident response planner. Respond only in valid JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=self.temperature,
                max_tokens=500,
                response_format={"type": "json_object"}  # Force JSON response
            )

            return response.choices[0].message.content

        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}")

    def _parse_llm_response(self, response: str) -> RunbookSelection:
        """Parse and validate LLM response"""

        try:
            data = json.loads(response)
            return RunbookSelection(**data)

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON from LLM: {e}")

        except Exception as e:
            raise ValueError(f"Failed to parse LLM response: {e}")

    def get_runbook_metadata(self, runbook_id: str) -> Optional[Dict]:
        """Get full runbook metadata for selected runbook"""
        return self.library.get_runbook(runbook_id)


class PlanningLog:
    """
    Audit trail for runbook planning decisions

    Every LLM call and runbook selection is logged for:
    - HIPAA audit trail (§164.312(b))
    - Incident post-mortems
    - Model performance monitoring
    - Evidence bundle generation
    """

    def __init__(self, log_dir: str = "./logs/planning"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_planning_decision(
        self,
        incident: Incident,
        selection: RunbookSelection,
        llm_prompt: str,
        llm_response: str,
        execution_started: bool = False
    ):
        """Log a planning decision for audit trail"""

        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "incident": incident.dict(),
            "selection": selection.dict(),
            "llm_prompt": llm_prompt,
            "llm_response": llm_response,
            "execution_started": execution_started
        }

        # Write to timestamped log file
        log_file = self.log_dir / f"{incident.client_id}_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"

        with open(log_file, 'a') as f:
            f.write(json.dumps(log_entry) + "\n")

    def get_planning_history(
        self,
        client_id: str,
        days: int = 30
    ) -> List[Dict]:
        """Retrieve planning history for a client"""

        history = []

        # Read all log files for client
        for log_file in self.log_dir.glob(f"{client_id}_*.jsonl"):
            with open(log_file, 'r') as f:
                for line in f:
                    entry = json.loads(line)
                    history.append(entry)

        # Sort by timestamp
        history.sort(key=lambda x: x['timestamp'], reverse=True)

        return history


# Example usage
if __name__ == "__main__":
    import asyncio

    async def test_planner():
        """Test planner with synthetic incident"""

        # Initialize planner
        planner = Planner(runbooks_dir="./runbooks")

        # Create synthetic incident
        incident = Incident(
            client_id="clinic-001",
            hostname="srv-primary",
            incident_type="backup_failure",
            severity="high",
            timestamp=datetime.utcnow().isoformat(),
            details={
                "last_successful_backup": "2025-10-23T02:00:00Z",
                "failure_reason": "Disk space insufficient",
                "disk_usage_percent": 94
            }
        )

        print(f"Incident: {incident.incident_type} on {incident.hostname}")
        print(f"Severity: {incident.severity}")
        print()

        # Select runbook
        try:
            selection = await planner.select_runbook(incident)

            print(f"Selected Runbook: {selection.runbook_id}")
            print(f"Confidence: {selection.confidence:.2%}")
            print(f"Reasoning: {selection.reasoning}")
            print(f"Human Approval Required: {selection.requires_human_approval}")

            if selection.alternative_runbooks:
                print(f"Alternatives: {', '.join(selection.alternative_runbooks)}")

            # Get full runbook metadata
            runbook = planner.get_runbook_metadata(selection.runbook_id)
            print()
            print(f"Runbook Details:")
            print(f"  Name: {runbook['name']}")
            print(f"  Steps: {len(runbook['steps'])}")
            print(f"  HIPAA: {', '.join(runbook['hipaa_controls'])}")

        except Exception as e:
            print(f"Planning failed: {e}")

    # Run test
    asyncio.run(test_planner())

"""
Learning Engine - Self-Improving Runbook System

This is the core of the self-learning system. It analyzes execution results
and uses LLMs to generate improved runbooks when failures occur.

Key principles:
1. Learn from every failure
2. Use LLMs to generate improvements
3. ALWAYS require human review before production
4. Track improvement over time
"""

from typing import Optional, Dict, Any
import json
import yaml
from datetime import datetime

from ..schemas.execution_result import ExecutionResult, FailureType, ExecutionStatus


class LearningEngine:
    """
    Learns from execution results to improve the system

    This is the magic that makes runbooks get better over time.
    Every failure is an opportunity to improve.
    """

    def __init__(
        self,
        llm_client: Any,  # LLMClient interface
        runbook_repo: Any,  # RunbookRepository interface
        review_queue: Any,  # ReviewQueue interface
        db: Any  # Database connection
    ):
        self.llm = llm_client
        self.runbooks = runbook_repo
        self.review_queue = review_queue
        self.db = db

    async def analyze_execution(self, result: ExecutionResult) -> Dict[str, Any]:
        """
        Main entry point: analyze an execution result

        This is called after EVERY runbook execution to learn from both
        successes and failures.

        Returns:
            dict: Analysis summary including any actions taken
        """
        analysis = {
            "execution_id": result.execution_id,
            "analyzed_at": datetime.utcnow().isoformat(),
            "actions_taken": []
        }

        # Learn from successes (pattern extraction)
        if result.success and result.verification_passed:
            success_patterns = await self._learn_from_success(result)
            analysis["success_patterns"] = success_patterns
            analysis["actions_taken"].append("pattern_extraction")

        # Learn from failures (runbook improvement)
        elif not result.success or not result.verification_passed:
            failure_analysis = await self._learn_from_failure(result)
            analysis["failure_analysis"] = failure_analysis
            if failure_analysis.get("improved_runbook_generated"):
                analysis["actions_taken"].append("runbook_improvement")

        # Store analysis in database
        await self.db.learning_analyses.insert_one(analysis)

        return analysis

    async def _learn_from_success(self, result: ExecutionResult) -> Dict[str, Any]:
        """
        Extract patterns from successful executions

        TODO Phase 4: Pattern extraction
        - Identify common success patterns
        - Build confidence metrics
        - Optimize execution time
        """
        return {
            "type": "success",
            "confidence": result.confidence,
            "execution_time": result.duration_seconds
        }

    async def _learn_from_failure(self, result: ExecutionResult) -> Dict[str, Any]:
        """
        Generate improved runbook from failures

        THIS IS THE CORE FEATURE

        Process:
        1. Categorize WHY the runbook failed
        2. If failure is fixable by improving the runbook, generate new version
        3. Queue improved runbook for human review
        4. Track improvement over time
        """
        failure_analysis = {
            "execution_id": result.execution_id,
            "analyzed_at": datetime.utcnow().isoformat()
        }

        # Step 1: Categorize the failure using LLM
        failure_type = await self._categorize_failure(result)
        result.failure_type = failure_type
        failure_analysis["failure_type"] = failure_type.value

        # Step 2: Only generate improved runbook for certain failure types
        improvable_types = [
            FailureType.RUNBOOK_INSUFFICIENT,
            FailureType.ENVIRONMENT_DIFF
        ]

        if failure_type in improvable_types:
            try:
                improved_runbook_id = await self._generate_improved_runbook(result)
                failure_analysis["improved_runbook_generated"] = True
                failure_analysis["improved_runbook_id"] = improved_runbook_id
            except Exception as e:
                failure_analysis["improved_runbook_generated"] = False
                failure_analysis["generation_error"] = str(e)
        else:
            failure_analysis["improved_runbook_generated"] = False
            failure_analysis["reason"] = f"Failure type '{failure_type.value}' not improvable via runbook changes"

        return failure_analysis

    async def _categorize_failure(self, result: ExecutionResult) -> FailureType:
        """
        Use LLM to determine WHY the runbook failed

        This is critical for deciding what action to take.
        Different failure types require different responses.

        Uses GPT-4o-mini (cheap model) for classification.
        """

        prompt = f"""Analyze this runbook execution failure and categorize the root cause.

INCIDENT TYPE: {result.incident_type}
RUNBOOK: {result.runbook_id}
PLATFORM: {result.platform}

ERROR DETAILS:
- Message: {result.error_message}
- Failed at step: {result.error_step}
- Retry count: {result.retry_count}

STATE BEFORE EXECUTION:
{json.dumps(result.state_before, indent=2)}

STATE AFTER EXECUTION:
{json.dumps(result.state_after, indent=2)}

STATE CHANGES:
{json.dumps(result.state_diff, indent=2)}

VERIFICATION:
- Method: {result.verification_method}
- Passed: {result.verification_passed}

EXECUTED STEPS:
{json.dumps([{
    "step": s.step_number,
    "action": s.action,
    "success": s.success,
    "error": s.error
} for s in result.executed_steps], indent=2)}

Categorize this failure as ONE of:
- wrong_diagnosis: We misclassified the incident type (e.g., thought it was disk full but was actually service crash)
- wrong_runbook: Right incident type, but wrong solution chosen (e.g., chose restart when needed config change)
- runbook_insufficient: Runbook is incomplete or has bugs (e.g., missing dependency check, incorrect parameters)
- environment_difference: Runbook is correct but environment is different (e.g., different OS version, missing tools)
- external_dependency: External service or resource unavailable (e.g., package repo down, network issue)
- permission_denied: Access/authentication issue (e.g., insufficient privileges, expired credentials)

Consider:
1. Did the right steps execute in the right order?
2. Did steps fail due to missing prerequisites?
3. Is this an environmental issue vs. runbook logic issue?
4. Could the runbook be improved to handle this case?

Respond with JSON only:
{{"failure_type": "one_of_the_types_above", "reasoning": "concise explanation", "confidence": 0.0-1.0}}
"""

        response = await self.llm.chat(
            model="gpt-4o-mini",  # Cheap model for classification
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.1  # Low temp for consistent classification
        )

        try:
            result_json = json.loads(response)
            failure_type = FailureType(result_json["failure_type"])

            # Store the LLM's reasoning
            await self.db.failure_classifications.insert_one({
                "execution_id": result.execution_id,
                "failure_type": failure_type.value,
                "reasoning": result_json.get("reasoning"),
                "confidence": result_json.get("confidence", 0.0),
                "classified_at": datetime.utcnow().isoformat()
            })

            return failure_type

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            # Fallback to generic classification
            return FailureType.RUNBOOK_INSUFFICIENT

    async def _generate_improved_runbook(self, result: ExecutionResult) -> str:
        """
        Use LLM to generate an improved version of the failed runbook

        THIS IS THE MAGIC

        Process:
        1. Get the original runbook
        2. Build rich context for LLM (failure details, state, etc.)
        3. Call GPT-4o (expensive but worth it) to generate improved runbook
        4. Validate the generated runbook
        5. Store with metadata
        6. Queue for human review (SAFETY CRITICAL)

        Returns:
            str: ID of the new improved runbook

        Raises:
            Exception: If generation or validation fails
        """

        # Step 1: Get the original runbook
        original_runbook = await self.runbooks.get(result.runbook_id)
        if not original_runbook:
            raise ValueError(f"Original runbook {result.runbook_id} not found")

        # Step 2: Build rich context for LLM
        context = self._build_improvement_prompt(result, original_runbook)

        # Step 3: Call LLM (use the GOOD model for this)
        response = await self.llm.chat(
            model="gpt-4o",  # Best model for code generation
            messages=[{"role": "user", "content": context}],
            max_tokens=2500,
            temperature=0.2  # Low temp for consistency
        )

        # Step 4: Parse and validate the improved runbook
        improved_runbook = self._extract_yaml_from_response(response)

        if not await self._validate_runbook(improved_runbook):
            raise ValueError("Generated runbook failed validation")

        # Step 5: Generate new runbook ID (version increment)
        new_runbook_id = self._generate_version_id(result.runbook_id)

        # Add metadata to track lineage
        improved_runbook["metadata"] = {
            "parent_runbook": result.runbook_id,
            "generated_from_failure": result.execution_id,
            "generated_at": datetime.utcnow().isoformat(),
            "generated_by": "learning_engine",
            "requires_human_review": True,
            "failure_type": result.failure_type.value if result.failure_type else None,
            "generation_model": "gpt-4o",
            "original_error": result.error_message
        }

        # Step 6: Save to database
        await self.runbooks.create(
            runbook_id=new_runbook_id,
            content=improved_runbook,
            status="pending_review"
        )

        # Step 7: Queue for human review (CRITICAL SAFETY STEP)
        await self.review_queue.add(
            runbook_id=new_runbook_id,
            reason="Generated from execution failure",
            failure_context=result
        )

        return new_runbook_id

    def _build_improvement_prompt(self, result: ExecutionResult, original: Dict[str, Any]) -> str:
        """
        Build the LLM prompt for runbook improvement

        This is the key to getting good results. We need to give the LLM:
        1. The original runbook
        2. Complete context about what failed
        3. Clear requirements for the output
        """

        return f"""You are a Senior Site Reliability Engineer analyzing a failed remediation runbook.

Your task is to generate an IMPROVED runbook that would have succeeded in this scenario.

# ORIGINAL RUNBOOK
```yaml
{yaml.dump(original, default_flow_style=False, sort_keys=False)}
```

# EXECUTION CONTEXT
- **Incident Type**: {result.incident_type}
- **Client**: {result.client_id}
- **Hostname**: {result.hostname}
- **Platform**: {result.platform}
- **Execution ID**: {result.execution_id}

# STATE BEFORE EXECUTION
```json
{json.dumps(result.state_before, indent=2)}
```

# STEPS EXECUTED (with outcomes)
```json
{json.dumps([{
    "step": s.step_number,
    "action": s.action,
    "duration": s.duration_seconds,
    "success": s.success,
    "output": s.output,
    "error": s.error
} for s in result.executed_steps], indent=2)}
```

# STATE AFTER EXECUTION
```json
{json.dumps(result.state_after, indent=2)}
```

# WHAT CHANGED
```json
{json.dumps(result.state_diff, indent=2)}
```

# FAILURE DETAILS
- **Failed at step**: {result.error_step}
- **Error message**: {result.error_message}
- **Failure type**: {result.failure_type.value if result.failure_type else 'unknown'}
- **Retry count**: {result.retry_count}

# VERIFICATION
- **Method**: {result.verification_method}
- **Result**: {result.verification_passed}

# YOUR TASK

Analyze why this runbook failed and generate an IMPROVED runbook that would succeed.

## Analysis Questions
1. **Missing Steps**: Are there prerequisite checks or setup steps missing?
2. **Wrong Order**: Are steps in the wrong sequence?
3. **Insufficient Error Handling**: Does the runbook need better error detection/recovery?
4. **Environment Assumptions**: Does the runbook assume something that wasn't true?
5. **Missing Verification**: Does the runbook need better verification steps?
6. **Wrong Parameters**: Are the parameters or configurations incorrect?

## Requirements for Improved Runbook

1. **Structure**: Keep the same YAML structure as the original
2. **ID**: Generate new ID with version suffix (e.g., RB-WIN-SERVICE-001-v2)
3. **Completeness**: Include ALL required fields:
   - id, name, description, platform, incident_types
   - severity, hipaa_controls (if present)
   - steps (with action, params, timeout, retry)
   - verification
   - rollback (if applicable)
   - evidence_required

4. **Documentation**: Add comments explaining:
   - What you changed and why
   - What the original missed
   - How this handles the failure case

5. **Safety**: Ensure the improved runbook:
   - Checks prerequisites before acting
   - Has proper error handling
   - Includes rollback steps
   - Verifies success properly

## Output Format

Generate ONLY the YAML runbook. No explanation before or after.
Use comments within the YAML to explain changes.

Begin your response with the YAML (you may use ```yaml code fence).
"""

    def _extract_yaml_from_response(self, response: str) -> Dict[str, Any]:
        """
        Extract YAML from LLM response (may be wrapped in markdown)

        LLMs often wrap code in markdown fences, so we need to handle that.
        """
        # Remove markdown code fences if present
        response = response.strip()

        # Remove ```yaml or ``` at start
        if response.startswith("```yaml"):
            response = response[7:]
        elif response.startswith("```"):
            response = response[3:]

        # Remove ``` at end
        if response.endswith("```"):
            response = response[:-3]

        # Parse YAML
        try:
            return yaml.safe_load(response.strip())
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse LLM-generated YAML: {e}")

    async def _validate_runbook(self, runbook: Dict[str, Any]) -> bool:
        """
        Validate the LLM-generated runbook is structurally valid

        This is a safety check to ensure we don't queue garbage for review.
        We're not validating correctness (that's for humans), just structure.
        """
        # Required top-level fields
        required_fields = ["id", "name", "platform", "incident_types", "steps"]

        for field in required_fields:
            if field not in runbook:
                return False

        # Validate incident_types is a list
        if not isinstance(runbook["incident_types"], list):
            return False

        # Validate steps structure
        if not isinstance(runbook["steps"], list):
            return False

        if len(runbook["steps"]) == 0:
            return False

        # Each step must have an action
        for step in runbook["steps"]:
            if "action" not in step:
                return False

        # If verification is present, validate structure
        if "verification" in runbook:
            verification = runbook["verification"]
            if "method" not in verification:
                return False

        return True

    def _generate_version_id(self, original_id: str) -> str:
        """
        Generate versioned runbook ID

        Examples:
        - RB-WIN-SERVICE-001 -> RB-WIN-SERVICE-001-v2
        - RB-WIN-SERVICE-001-v2 -> RB-WIN-SERVICE-001-v3
        """
        # Check if already versioned
        if "-v" in original_id:
            # Increment version
            base, version_str = original_id.rsplit("-v", 1)
            try:
                version = int(version_str)
                new_version = version + 1
                return f"{base}-v{new_version}"
            except ValueError:
                # Malformed version, treat as unversioned
                return f"{original_id}-v2"
        else:
            return f"{original_id}-v2"

    async def get_improvement_stats(self, runbook_id: str) -> Dict[str, Any]:
        """
        Get statistics about runbook improvements over time

        Useful for dashboard and metrics:
        - How many versions exist?
        - What's the success rate trend?
        - How many failures triggered improvements?
        """
        # Get all versions of this runbook
        base_id = runbook_id.split("-v")[0]  # Remove version suffix

        versions = await self.db.runbooks.find({
            "id": {"$regex": f"^{base_id}"}
        }).to_list(length=None)

        # Get execution results for each version
        stats = {
            "base_runbook_id": base_id,
            "total_versions": len(versions),
            "versions": []
        }

        for version in versions:
            version_id = version["id"]

            # Get executions for this version
            executions = await self.db.execution_results.find({
                "runbook_id": version_id
            }).to_list(length=None)

            if executions:
                success_count = sum(1 for e in executions if e.get("success"))
                total_count = len(executions)
                success_rate = (success_count / total_count) * 100 if total_count > 0 else 0

                stats["versions"].append({
                    "runbook_id": version_id,
                    "total_executions": total_count,
                    "successful_executions": success_count,
                    "success_rate": round(success_rate, 2),
                    "created_at": version.get("metadata", {}).get("generated_at")
                })

        return stats

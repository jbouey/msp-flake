"""
Review API Endpoints - Human Review Interface

Provides REST API for reviewing LLM-generated runbooks.

Endpoints:
- GET /api/review/pending - List pending reviews
- GET /api/review/runbook/{id} - Get runbook details for review
- POST /api/review/approve/{id} - Approve a runbook
- POST /api/review/reject/{id} - Reject a runbook
- POST /api/review/test/{id} - Add test result
- GET /api/review/stats - Get queue statistics
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

from ..review.review_queue import ReviewQueue, ReviewStatus, ReviewPriority


router = APIRouter(prefix="/api/review", tags=["review"])


# Request/Response Models
class ApprovalRequest(BaseModel):
    """Request to approve a runbook"""
    reviewer: str = Field(..., description="Username/email of reviewer")
    notes: Optional[str] = Field(None, description="Optional approval notes")


class RejectionRequest(BaseModel):
    """Request to reject a runbook"""
    reviewer: str = Field(..., description="Username/email of reviewer")
    reason: str = Field(..., description="Why runbook was rejected")


class TestResultRequest(BaseModel):
    """Request to add a test result"""
    test_name: str = Field(..., description="Name of the test")
    passed: bool = Field(..., description="Whether test passed")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional test details")


class ChangesRequest(BaseModel):
    """Request changes to a runbook"""
    reviewer: str = Field(..., description="Username/email of reviewer")
    requested_changes: str = Field(..., description="Description of needed changes")


# Endpoints
@router.get("/pending")
async def get_pending_reviews(
    priority: Optional[str] = Query(None, description="Filter by priority (high/medium/low)"),
    limit: int = Query(50, description="Maximum number to return")
):
    """
    Get all runbooks pending review

    Returns list sorted by priority (high first) then age (oldest first).
    """
    queue = ReviewQueue(db)

    priority_enum = None
    if priority:
        try:
            priority_enum = ReviewPriority(priority)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid priority: {priority}")

    pending = await queue.get_pending(priority=priority_enum, limit=limit)

    return {
        "count": len(pending),
        "pending_reviews": pending
    }


@router.get("/runbook/{runbook_id}")
async def get_runbook_details(runbook_id: str):
    """
    Get comprehensive details for reviewing a runbook

    Returns:
    - The runbook content
    - Parent runbook (for comparison)
    - Failure context that triggered generation
    - Test results (if any)
    - Review status
    """
    # Get the runbook
    runbook = await db.runbooks.find_one({"id": runbook_id})
    if not runbook:
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not found")

    # Get review queue item
    queue_item = await db.review_queue.find_one({"runbook_id": runbook_id})

    # Get metadata about generation
    metadata = runbook.get("metadata", {})
    failure_execution_id = metadata.get("generated_from_failure")

    # Get failure context (the execution that triggered generation)
    failure_context = None
    if failure_execution_id:
        failure_context = await db.execution_results.find_one(
            {"execution_id": failure_execution_id}
        )

    # Get parent runbook for side-by-side comparison
    parent_id = metadata.get("parent_runbook")
    parent_runbook = None
    if parent_id:
        parent_runbook = await db.runbooks.find_one({"id": parent_id})

    # Get execution history for parent (to see why it was failing)
    parent_executions = []
    if parent_id:
        parent_executions = await db.execution_results.find({
            "runbook_id": parent_id
        }).sort("started_at", -1).limit(10).to_list(length=10)

    return {
        "runbook": runbook,
        "parent_runbook": parent_runbook,
        "failure_context": failure_context,
        "parent_execution_history": parent_executions,
        "metadata": metadata,
        "review_status": queue_item,
        "generated_at": metadata.get("generated_at"),
        "generation_model": metadata.get("generation_model")
    }


@router.post("/approve/{runbook_id}")
async def approve_runbook(runbook_id: str, request: ApprovalRequest):
    """
    Approve a runbook for production use

    This activates the runbook and makes it available for selection.
    """
    queue = ReviewQueue(db)

    # Check if runbook exists and is pending review
    queue_item = await queue.get_item(runbook_id)
    if not queue_item:
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not in review queue")

    if queue_item["status"] == ReviewStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail="Runbook already approved")

    # Approve
    await queue.approve(
        runbook_id=runbook_id,
        reviewer=request.reviewer,
        notes=request.notes
    )

    return {
        "status": "approved",
        "runbook_id": runbook_id,
        "approved_by": request.reviewer,
        "approved_at": datetime.utcnow().isoformat()
    }


@router.post("/reject/{runbook_id}")
async def reject_runbook(runbook_id: str, request: RejectionRequest):
    """
    Reject a runbook

    The runbook is archived but not deleted (for audit/learning).
    """
    queue = ReviewQueue(db)

    # Check if runbook exists and is pending review
    queue_item = await queue.get_item(runbook_id)
    if not queue_item:
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not in review queue")

    if queue_item["status"] == ReviewStatus.REJECTED.value:
        raise HTTPException(status_code=400, detail="Runbook already rejected")

    # Reject
    await queue.reject(
        runbook_id=runbook_id,
        reviewer=request.reviewer,
        reason=request.reason
    )

    return {
        "status": "rejected",
        "runbook_id": runbook_id,
        "rejected_by": request.reviewer,
        "rejected_at": datetime.utcnow().isoformat(),
        "reason": request.reason
    }


@router.post("/changes/{runbook_id}")
async def request_changes(runbook_id: str, request: ChangesRequest):
    """
    Request changes to a runbook

    Marks runbook as needing changes. Can be re-generated or manually edited.
    """
    queue = ReviewQueue(db)

    # Check if runbook exists
    queue_item = await queue.get_item(runbook_id)
    if not queue_item:
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not in review queue")

    # Request changes
    await queue.request_changes(
        runbook_id=runbook_id,
        reviewer=request.reviewer,
        requested_changes=request.requested_changes
    )

    return {
        "status": "changes_requested",
        "runbook_id": runbook_id,
        "reviewed_by": request.reviewer,
        "requested_changes": request.requested_changes
    }


@router.post("/test/{runbook_id}")
async def add_test_result(runbook_id: str, request: TestResultRequest):
    """
    Add a test result for a runbook under review

    Allows tracking of testing progress before approval.
    """
    queue = ReviewQueue(db)

    # Check if runbook exists
    queue_item = await queue.get_item(runbook_id)
    if not queue_item:
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not in review queue")

    # Add test result
    await queue.add_test_result(
        runbook_id=runbook_id,
        test_name=request.test_name,
        passed=request.passed,
        details=request.details
    )

    return {
        "status": "test_result_added",
        "runbook_id": runbook_id,
        "test_name": request.test_name,
        "passed": request.passed
    }


@router.get("/stats")
async def get_queue_stats():
    """
    Get statistics about the review queue

    Useful for dashboards and monitoring.
    """
    queue = ReviewQueue(db)
    stats = await queue.get_stats()

    return {
        "queue_stats": stats,
        "generated_at": datetime.utcnow().isoformat()
    }


@router.get("/history")
async def get_review_history(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, description="Maximum number to return")
):
    """
    Get review history (approved/rejected runbooks)

    Useful for audit and learning from past reviews.
    """
    queue = ReviewQueue(db)

    status_enum = None
    if status:
        try:
            status_enum = ReviewStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    if status_enum:
        items = await queue.get_by_status(status=status_enum, limit=limit)
    else:
        # Get all completed reviews (approved + rejected)
        approved = await queue.get_by_status(status=ReviewStatus.APPROVED, limit=limit//2)
        rejected = await queue.get_by_status(status=ReviewStatus.REJECTED, limit=limit//2)
        items = approved + rejected

        # Sort by review date
        items.sort(key=lambda x: x.get("reviewed_at", ""), reverse=True)

    return {
        "count": len(items),
        "history": items
    }


@router.get("/comparison/{runbook_id}")
async def get_runbook_comparison(runbook_id: str):
    """
    Get side-by-side comparison of improved runbook vs. parent

    Useful for review UI to show exactly what changed.
    """
    # Get the improved runbook
    runbook = await db.runbooks.find_one({"id": runbook_id})
    if not runbook:
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not found")

    # Get parent
    metadata = runbook.get("metadata", {})
    parent_id = metadata.get("parent_runbook")

    if not parent_id:
        raise HTTPException(status_code=400, detail="Runbook has no parent (not LLM-generated)")

    parent_runbook = await db.runbooks.find_one({"id": parent_id})
    if not parent_runbook:
        raise HTTPException(status_code=404, detail=f"Parent runbook {parent_id} not found")

    # Compute differences
    differences = _compute_differences(parent_runbook, runbook)

    return {
        "parent": parent_runbook,
        "improved": runbook,
        "differences": differences,
        "generation_context": {
            "failure_execution_id": metadata.get("generated_from_failure"),
            "failure_type": metadata.get("failure_type"),
            "generated_at": metadata.get("generated_at"),
            "generation_model": metadata.get("generation_model")
        }
    }


def _compute_differences(parent: Dict[str, Any], improved: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute differences between parent and improved runbook

    Returns structured diff for display in UI.
    """
    differences = {
        "steps_added": [],
        "steps_removed": [],
        "steps_modified": [],
        "verification_changed": False,
        "other_changes": []
    }

    # Compare steps
    parent_steps = parent.get("steps", [])
    improved_steps = improved.get("steps", [])

    if len(parent_steps) != len(improved_steps):
        differences["other_changes"].append(
            f"Step count changed: {len(parent_steps)} -> {len(improved_steps)}"
        )

    # Simple step comparison (could be more sophisticated)
    for i, parent_step in enumerate(parent_steps):
        if i < len(improved_steps):
            improved_step = improved_steps[i]
            if parent_step.get("action") != improved_step.get("action"):
                differences["steps_modified"].append({
                    "step_number": i + 1,
                    "parent_action": parent_step.get("action"),
                    "improved_action": improved_step.get("action")
                })

    # Compare verification
    if parent.get("verification") != improved.get("verification"):
        differences["verification_changed"] = True
        differences["verification_details"] = {
            "parent": parent.get("verification"),
            "improved": improved.get("verification")
        }

    return differences


# Initialize database connection (will be injected by main app)
db = None


def init_review_api(database):
    """Initialize the review API with database connection"""
    global db
    db = database

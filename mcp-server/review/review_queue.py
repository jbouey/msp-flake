"""
Review Queue - Human Approval Workflow for LLM-Generated Runbooks

SAFETY CRITICAL: No LLM-generated runbook executes without human approval.

This is the safety gate that prevents bad runbooks from reaching production.
Every LLM-generated runbook must be reviewed, tested, and explicitly approved
by a human before it can be used for remediation.
"""

import os
import ssl
import smtplib
import logging
import time as _time
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from ..schemas.execution_result import ExecutionResult

logger = logging.getLogger(__name__)

# SMTP Configuration (shared with email_alerts.py via env vars)
SMTP_HOST = os.getenv("SMTP_HOST", "mail.privateemail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "alerts@osiriscare.net")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "administrator@osiriscare.net")


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _send_review_email(subject: str, text_body: str, html_body: str) -> bool:
    """Send a review notification email.

    Args:
        subject: Email subject line
        text_body: Plain text version of the email
        html_body: HTML version of the email

    Returns:
        True if sent successfully, False otherwise
    """
    if not (SMTP_USER and SMTP_PASSWORD):
        logger.warning("SMTP not configured (SMTP_USER/SMTP_PASSWORD not set) - review email not sent: %s", subject)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = ALERT_EMAIL
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    max_retries = 2
    for attempt in range(max_retries):
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, ALERT_EMAIL, msg.as_string())
            logger.info("Review email sent: %s", subject)
            return True
        except (smtplib.SMTPException, OSError) as smtp_err:
            if attempt < max_retries - 1:
                logger.warning("SMTP attempt %d/%d failed: %s", attempt + 1, max_retries, smtp_err)
                _time.sleep(2)  # 2s backoff
            else:
                logger.error("Failed to send review email after %d attempts: %s", max_retries, smtp_err)
                return False

    return False


class ReviewStatus(str, Enum):
    """Status of a runbook in the review queue"""
    PENDING_REVIEW = "pending_review"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"


class ReviewPriority(str, Enum):
    """Priority levels for review"""
    HIGH = "high"        # Critical incident type, blocking production
    MEDIUM = "medium"    # Standard improvement
    LOW = "low"          # Optimization, nice-to-have


class ReviewQueueItem:
    """
    Single item in the review queue

    Tracks the lifecycle of a runbook from generation to approval/rejection.
    """

    def __init__(
        self,
        runbook_id: str,
        reason: str,
        priority: ReviewPriority = ReviewPriority.MEDIUM,
        failure_context: Optional[ExecutionResult] = None
    ):
        self.runbook_id = runbook_id
        self.status = ReviewStatus.PENDING_REVIEW
        self.priority = priority
        self.reason = reason
        self.failure_context = failure_context
        self.created_at = datetime.utcnow()

        # Review tracking
        self.reviewed_by: Optional[str] = None
        self.reviewed_at: Optional[datetime] = None
        self.approval_notes: Optional[str] = None
        self.rejection_reason: Optional[str] = None

        # Testing tracking
        self.test_results: List[Dict[str, Any]] = []
        self.test_passed: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            "runbook_id": self.runbook_id,
            "status": self.status.value,
            "priority": self.priority.value,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "approval_notes": self.approval_notes,
            "rejection_reason": self.rejection_reason,
            "test_results": self.test_results,
            "test_passed": self.test_passed,
            "failure_execution_id": self.failure_context.execution_id if self.failure_context else None
        }


class ReviewQueue:
    """
    Manages human review of LLM-generated runbooks

    SAFETY CRITICAL: This is the gate between LLM generation and production.

    Workflow:
    1. LLM generates improved runbook
    2. Runbook added to review queue
    3. Human reviewer notified
    4. Human reviews, tests, and approves/rejects
    5. Approved runbooks activated for production use
    6. Rejected runbooks archived with reason
    """

    def __init__(self, db: Any):
        """
        Args:
            db: Database connection
        """
        self.db = db

    async def add(
        self,
        runbook_id: str,
        reason: str,
        priority: ReviewPriority = ReviewPriority.MEDIUM,
        failure_context: Optional[ExecutionResult] = None
    ) -> str:
        """
        Add a runbook to the review queue

        Args:
            runbook_id: ID of the runbook to review
            reason: Why this runbook needs review
            priority: Priority level (high/medium/low)
            failure_context: Execution result that triggered generation

        Returns:
            str: Queue item ID
        """
        item = ReviewQueueItem(
            runbook_id=runbook_id,
            reason=reason,
            priority=priority,
            failure_context=failure_context
        )

        # Insert into database
        result = await self.db.review_queue.insert_one(item.to_dict())

        # Notify human reviewer
        await self._notify_reviewer(item)

        return str(result.inserted_id)

    async def get_pending(
        self,
        priority: Optional[ReviewPriority] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get all runbooks pending review

        Args:
            priority: Filter by priority (optional)
            limit: Maximum number to return

        Returns:
            List of pending review items
        """
        query = {"status": ReviewStatus.PENDING_REVIEW.value}

        if priority:
            query["priority"] = priority.value

        # Sort by priority (high first) then creation time (oldest first)
        priority_order = {"high": 1, "medium": 2, "low": 3}

        items = await self.db.review_queue.find(query).to_list(length=limit)

        # Sort in Python (simpler than MongoDB aggregation)
        items.sort(key=lambda x: (
            priority_order.get(x.get("priority", "medium"), 2),
            x.get("created_at", "")
        ))

        return items

    async def get_item(self, runbook_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific review queue item

        Args:
            runbook_id: Runbook ID

        Returns:
            Review item dict or None
        """
        return await self.db.review_queue.find_one({"runbook_id": runbook_id})

    async def get_by_status(
        self,
        status: ReviewStatus,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get items by status

        Args:
            status: Status to filter by
            limit: Maximum number to return

        Returns:
            List of review items
        """
        return await self.db.review_queue.find({
            "status": status.value
        }).to_list(length=limit)

    async def assign_reviewer(
        self,
        runbook_id: str,
        reviewer: str
    ) -> None:
        """
        Assign a reviewer and mark as in-review

        Args:
            runbook_id: Runbook to assign
            reviewer: Username/email of reviewer
        """
        await self.db.review_queue.update_one(
            {"runbook_id": runbook_id},
            {
                "$set": {
                    "status": ReviewStatus.IN_REVIEW.value,
                    "reviewed_by": reviewer,
                    "review_started_at": datetime.utcnow().isoformat()
                }
            }
        )

    async def add_test_result(
        self,
        runbook_id: str,
        test_name: str,
        passed: bool,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Add a test result for a runbook under review

        Args:
            runbook_id: Runbook being tested
            test_name: Name of the test
            passed: Whether test passed
            details: Additional test details
        """
        test_result = {
            "test_name": test_name,
            "passed": passed,
            "tested_at": datetime.utcnow().isoformat(),
            "details": details or {}
        }

        await self.db.review_queue.update_one(
            {"runbook_id": runbook_id},
            {
                "$push": {"test_results": test_result}
            }
        )

    async def approve(
        self,
        runbook_id: str,
        reviewer: str,
        notes: Optional[str] = None
    ) -> None:
        """
        Approve a runbook - it can now go into production

        This is the green light for the runbook to be used.

        Args:
            runbook_id: Runbook to approve
            reviewer: Username/email of reviewer
            notes: Optional approval notes
        """
        await self.db.review_queue.update_one(
            {"runbook_id": runbook_id},
            {
                "$set": {
                    "status": ReviewStatus.APPROVED.value,
                    "reviewed_by": reviewer,
                    "reviewed_at": datetime.utcnow().isoformat(),
                    "approval_notes": notes
                }
            }
        )

        # Activate the runbook (make it available for selection)
        await self._activate_runbook(runbook_id)

        # Notify stakeholders of approval
        await self._notify_approval(runbook_id, reviewer, notes)

    async def reject(
        self,
        runbook_id: str,
        reviewer: str,
        reason: str
    ) -> None:
        """
        Reject a runbook - it stays dormant

        The runbook is archived but not deleted (for learning).

        Args:
            runbook_id: Runbook to reject
            reviewer: Username/email of reviewer
            reason: Why it was rejected
        """
        await self.db.review_queue.update_one(
            {"runbook_id": runbook_id},
            {
                "$set": {
                    "status": ReviewStatus.REJECTED.value,
                    "reviewed_by": reviewer,
                    "reviewed_at": datetime.utcnow().isoformat(),
                    "rejection_reason": reason
                }
            }
        )

        # Mark runbook as rejected (not deleted, for audit)
        await self.db.runbooks.update_one(
            {"id": runbook_id},
            {
                "$set": {
                    "status": "rejected",
                    "rejected_at": datetime.utcnow().isoformat(),
                    "rejected_by": reviewer,
                    "rejection_reason": reason
                }
            }
        )

    async def request_changes(
        self,
        runbook_id: str,
        reviewer: str,
        requested_changes: str
    ) -> None:
        """
        Request changes to a runbook

        Marks runbook as needing changes. Can be re-generated or manually edited.

        Args:
            runbook_id: Runbook needing changes
            reviewer: Username/email of reviewer
            requested_changes: Description of needed changes
        """
        await self.db.review_queue.update_one(
            {"runbook_id": runbook_id},
            {
                "$set": {
                    "status": ReviewStatus.NEEDS_CHANGES.value,
                    "reviewed_by": reviewer,
                    "reviewed_at": datetime.utcnow().isoformat(),
                    "requested_changes": requested_changes
                }
            }
        )

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the review queue

        Useful for dashboards and metrics.

        Returns:
            Statistics dictionary
        """
        # Count by status
        pending_count = await self.db.review_queue.count_documents({
            "status": ReviewStatus.PENDING_REVIEW.value
        })

        in_review_count = await self.db.review_queue.count_documents({
            "status": ReviewStatus.IN_REVIEW.value
        })

        approved_count = await self.db.review_queue.count_documents({
            "status": ReviewStatus.APPROVED.value
        })

        rejected_count = await self.db.review_queue.count_documents({
            "status": ReviewStatus.REJECTED.value
        })

        # Count by priority (pending only)
        high_priority = await self.db.review_queue.count_documents({
            "status": ReviewStatus.PENDING_REVIEW.value,
            "priority": ReviewPriority.HIGH.value
        })

        return {
            "pending_review": pending_count,
            "in_review": in_review_count,
            "approved": approved_count,
            "rejected": rejected_count,
            "high_priority_pending": high_priority,
            "total": pending_count + in_review_count + approved_count + rejected_count
        }

    async def _activate_runbook(self, runbook_id: str) -> None:
        """
        Activate a runbook for production use

        Changes status from pending_review to active.
        """
        await self.db.runbooks.update_one(
            {"id": runbook_id},
            {
                "$set": {
                    "status": "active",
                    "activated_at": datetime.utcnow().isoformat()
                }
            }
        )

    async def _notify_reviewer(self, item: ReviewQueueItem) -> None:
        """
        Notify human that review is needed via DB record and email.
        """
        review_url = "https://dashboard.osiriscare.net/admin/learning"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Log to database (existing behavior)
        notification = {
            "type": "review_needed",
            "runbook_id": item.runbook_id,
            "priority": item.priority.value,
            "reason": item.reason,
            "created_at": item.created_at.isoformat(),
            "review_url": review_url,
        }
        await self.db.notifications.insert_one(notification)

        # Send email notification
        subject = f"[REVIEW NEEDED] Runbook {item.runbook_id} requires approval"

        priority_upper = item.priority.value.upper()
        priority_colors = {
            "HIGH": ("#dc2626", "#ea580c"),
            "MEDIUM": ("#f59e0b", "#eab308"),
            "LOW": ("#3b82f6", "#6366f1"),
        }
        color_start, color_end = priority_colors.get(priority_upper, ("#f59e0b", "#eab308"))

        text_body = f"""REVIEW NEEDED - OsirisCare Central Command
{'=' * 44}

Runbook:  {item.runbook_id}
Priority: {priority_upper}
Time:     {timestamp}

Reason:
{item.reason}

Please review this runbook and approve or reject it:
{review_url}

---
This is an automated alert from OsirisCare Central Command.
Dashboard: https://dashboard.osiriscare.net
"""

        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, {color_start}, {color_end}); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .header h1 {{ margin: 0; font-size: 22px; }}
        .header .badge {{ display: inline-block; background: rgba(255,255,255,0.2); padding: 2px 10px; border-radius: 12px; font-size: 13px; margin-top: 6px; }}
        .content {{ background: #f9fafb; padding: 20px; border: 1px solid #e5e7eb; }}
        .field {{ margin-bottom: 12px; }}
        .field-label {{ font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase; }}
        .field-value {{ color: #111827; }}
        .reason-box {{ background: white; padding: 16px; border-radius: 8px; border-left: 4px solid {color_start}; margin-top: 16px; }}
        .footer {{ padding: 16px 20px; background: #f3f4f6; border-radius: 0 0 8px 8px; font-size: 12px; color: #6b7280; }}
        .button {{ display: inline-block; background: #3b82f6; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; margin-top: 16px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Review Needed</h1>
            <span class="badge">{priority_upper} PRIORITY</span>
        </div>
        <div class="content">
            <div class="field">
                <div class="field-label">Runbook ID</div>
                <div class="field-value" style="font-size: 18px; font-weight: 600;">{_escape_html(item.runbook_id)}</div>
            </div>
            <div class="field">
                <div class="field-label">Priority</div>
                <div class="field-value">{_escape_html(priority_upper)}</div>
            </div>
            <div class="field">
                <div class="field-label">Time</div>
                <div class="field-value">{timestamp}</div>
            </div>
            <div class="reason-box">
                <div class="field-label">Reason for Review</div>
                <div class="field-value" style="margin-top: 4px;">{_escape_html(item.reason)}</div>
            </div>
            <a href="{review_url}" class="button">Review in Dashboard</a>
        </div>
        <div class="footer">
            This is an automated alert from OsirisCare Central Command.<br>
            <a href="https://dashboard.osiriscare.net">dashboard.osiriscare.net</a>
        </div>
    </div>
</body>
</html>
"""

        _send_review_email(subject, text_body, html_body)

    async def _notify_approval(
        self,
        runbook_id: str,
        reviewer: str,
        notes: Optional[str]
    ) -> None:
        """
        Notify stakeholders of runbook approval via DB record and email.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Log to database (existing behavior)
        notification = {
            "type": "runbook_approved",
            "runbook_id": runbook_id,
            "reviewed_by": reviewer,
            "notes": notes,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.db.notifications.insert_one(notification)

        # Send email notification
        subject = f"[APPROVED] Runbook {runbook_id} approved by {reviewer}"

        notes_text = notes if notes else "No additional notes."

        text_body = f"""RUNBOOK APPROVED - OsirisCare Central Command
{'=' * 44}

Runbook:  {runbook_id}
Approved: {timestamp}
Reviewer: {reviewer}

Notes:
{notes_text}

The runbook has been activated and is now available for production use.

---
This is an automated alert from OsirisCare Central Command.
Dashboard: https://dashboard.osiriscare.net
"""

        notes_html = ""
        if notes:
            notes_html = f"""
            <div style="background: white; padding: 16px; border-radius: 8px; border-left: 4px solid #059669; margin-top: 16px;">
                <div class="field-label">Approval Notes</div>
                <div class="field-value" style="margin-top: 4px;">{_escape_html(notes)}</div>
            </div>"""

        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #059669, #047857); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .header h1 {{ margin: 0; font-size: 22px; }}
        .header .badge {{ display: inline-block; background: rgba(255,255,255,0.2); padding: 2px 10px; border-radius: 12px; font-size: 13px; margin-top: 6px; }}
        .content {{ background: #f9fafb; padding: 20px; border: 1px solid #e5e7eb; }}
        .field {{ margin-bottom: 12px; }}
        .field-label {{ font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase; }}
        .field-value {{ color: #111827; }}
        .footer {{ padding: 16px 20px; background: #f3f4f6; border-radius: 0 0 8px 8px; font-size: 12px; color: #6b7280; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Runbook Approved</h1>
            <span class="badge">ACTIVATED</span>
        </div>
        <div class="content">
            <div class="field">
                <div class="field-label">Runbook ID</div>
                <div class="field-value" style="font-size: 18px; font-weight: 600;">{_escape_html(runbook_id)}</div>
            </div>
            <div class="field">
                <div class="field-label">Approved By</div>
                <div class="field-value">{_escape_html(reviewer)}</div>
            </div>
            <div class="field">
                <div class="field-label">Approved At</div>
                <div class="field-value">{timestamp}</div>
            </div>{notes_html}
            <div style="background: #ecfdf5; padding: 12px 16px; border-radius: 8px; margin-top: 16px; color: #065f46;">
                This runbook has been activated and is now available for production remediation.
            </div>
        </div>
        <div class="footer">
            This is an automated alert from OsirisCare Central Command.<br>
            <a href="https://dashboard.osiriscare.net">dashboard.osiriscare.net</a>
        </div>
    </div>
</body>
</html>
"""

        _send_review_email(subject, text_body, html_body)

    async def cleanup_old_items(self, days: int = 90) -> int:
        """
        Archive old approved/rejected items

        Keeps the queue manageable while preserving audit trail.

        Args:
            days: Archive items older than this many days

        Returns:
            Number of items archived
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Move to archive collection
        old_items = await self.db.review_queue.find({
            "status": {"$in": [ReviewStatus.APPROVED.value, ReviewStatus.REJECTED.value]},
            "reviewed_at": {"$lt": cutoff_date.isoformat()}
        }).to_list(length=None)

        if old_items:
            # Copy to archive
            await self.db.review_queue_archive.insert_many(old_items)

            # Delete from active queue
            await self.db.review_queue.delete_many({
                "_id": {"$in": [item["_id"] for item in old_items]}
            })

        return len(old_items)

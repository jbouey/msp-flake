"""
Federal Register HIPAA Monitoring.

Checks the Federal Register API for new HIPAA-related regulations,
notices, and proposed rules. Core compliance feature for staying
current with regulatory changes.

API Documentation: https://www.federalregister.gov/developers/documentation/api/v1
"""

import aiohttp
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class RegulatoryUpdate:
    """Represents a Federal Register document."""
    document_number: str
    title: str
    publication_date: str
    type: str  # Rule, Proposed Rule, Notice
    citation: str
    abstract: str
    html_url: str
    pdf_url: str
    agencies: List[str]
    cfr_references: List[str]
    docket_id: Optional[str] = None
    comment_due_date: Optional[str] = None
    effective_date: Optional[str] = None


class FederalRegisterMonitor:
    """
    Monitor Federal Register for HIPAA-related updates.

    Checks daily for new:
    - Final Rules
    - Proposed Rules (NPRMs)
    - Notices
    - Guidance documents

    Focuses on HHS/OCR publications related to HIPAA.
    """

    BASE_URL = "https://www.federalregister.gov/api/v1"

    def __init__(
        self,
        cache_dir: str = "/var/lib/msp-compliance-agent/regulatory",
        lookback_days: int = 30
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lookback_days = lookback_days

    async def check_for_updates(self) -> List[RegulatoryUpdate]:
        """
        Check Federal Register for new HIPAA documents.

        Returns:
            List of new regulatory updates
        """
        logger.info("Checking Federal Register for HIPAA updates")

        # Search parameters
        # Focus on HHS (Health and Human Services) documents about HIPAA
        params = {
            "conditions[term]": "HIPAA OR \"Health Insurance Portability\" OR \"electronic protected health information\"",
            "conditions[agencies][]": "health-and-human-services-department",
            "conditions[publication_date][gte]": (datetime.now(timezone.utc) - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d"),
            "order": "newest",
            "per_page": 20,
            "fields[]": [
                "document_number",
                "title",
                "publication_date",
                "type",
                "citation",
                "abstract",
                "html_url",
                "pdf_url",
                "agencies",
                "cfr_references",
                "docket_id",
                "dates"
            ]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}/documents.json",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"Federal Register API returned {resp.status}")
                        return []

                    data = await resp.json()
                    results = data.get("results", [])

                    logger.info(f"Found {len(results)} HIPAA-related documents")

                    updates = []
                    for doc in results:
                        update = self._parse_document(doc)
                        if update and not self._is_cached(update):
                            updates.append(update)
                            await self._cache_update(update)

                    return updates

        except Exception as e:
            logger.exception(f"Error checking Federal Register: {e}")
            return []

    def _parse_document(self, doc: Dict) -> Optional[RegulatoryUpdate]:
        """Parse Federal Register API response into RegulatoryUpdate."""
        try:
            # Extract dates
            dates = doc.get("dates", {})
            comment_due_date = None
            effective_date = None

            if isinstance(dates, dict):
                comment_due_date = dates.get("comments_close_on")
                effective_date = dates.get("effective_on")

            return RegulatoryUpdate(
                document_number=doc["document_number"],
                title=doc["title"],
                publication_date=doc["publication_date"],
                type=doc["type"],
                citation=doc.get("citation", ""),
                abstract=doc.get("abstract", ""),
                html_url=doc["html_url"],
                pdf_url=doc.get("pdf_url", ""),
                agencies=[a["name"] for a in doc.get("agencies", [])],
                cfr_references=doc.get("cfr_references", []),
                docket_id=doc.get("docket_id"),
                comment_due_date=comment_due_date,
                effective_date=effective_date
            )
        except Exception as e:
            logger.error(f"Error parsing document: {e}")
            return None

    def _is_cached(self, update: RegulatoryUpdate) -> bool:
        """Check if update has been seen before."""
        cache_file = self.cache_dir / f"{update.document_number}.json"
        return cache_file.exists()

    async def _cache_update(self, update: RegulatoryUpdate):
        """Cache update to prevent duplicate alerts."""
        cache_file = self.cache_dir / f"{update.document_number}.json"
        with open(cache_file, "w") as f:
            json.dump(asdict(update), f, indent=2)

    async def get_latest_updates(self, limit: int = 10) -> List[RegulatoryUpdate]:
        """Get cached updates sorted by publication date."""
        updates = []

        for cache_file in sorted(self.cache_dir.glob("*.json"), reverse=True):
            if len(updates) >= limit:
                break

            try:
                with open(cache_file, "r") as f:
                    data = json.load(f)
                    updates.append(RegulatoryUpdate(**data))
            except Exception as e:
                logger.error(f"Error reading cached update: {e}")

        return updates

    async def get_active_comment_periods(self) -> List[RegulatoryUpdate]:
        """Get proposed rules with active comment periods."""
        updates = await self.get_latest_updates(limit=50)
        today = datetime.now(timezone.utc).date()

        active = []
        for update in updates:
            if update.type == "Proposed Rule" and update.comment_due_date:
                try:
                    due_date = datetime.fromisoformat(update.comment_due_date).date()
                    if due_date >= today:
                        active.append(update)
                except:
                    pass

        return active

    async def generate_compliance_alert(self) -> Dict[str, Any]:
        """
        Generate compliance alert for dashboard.

        Returns:
            Alert dict with new updates and active comment periods
        """
        new_updates = await self.check_for_updates()
        active_comments = await self.get_active_comment_periods()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "new_updates_count": len(new_updates),
            "new_updates": [asdict(u) for u in new_updates[:5]],  # Top 5 new
            "active_comment_periods_count": len(active_comments),
            "active_comment_periods": [asdict(u) for u in active_comments],
            "requires_attention": len(new_updates) > 0 or len(active_comments) > 0
        }


async def run_monitor_daemon(interval_hours: int = 24):
    """
    Run Federal Register monitor as background daemon.

    Args:
        interval_hours: Hours between checks (default: daily)
    """
    monitor = FederalRegisterMonitor()

    while True:
        try:
            logger.info("Running Federal Register check")
            alert = await monitor.generate_compliance_alert()

            # Write alert for web UI to consume
            alert_file = Path("/var/lib/msp-compliance-agent/regulatory_alert.json")
            with open(alert_file, "w") as f:
                json.dump(alert, f, indent=2)

            if alert["requires_attention"]:
                logger.warning(f"New regulatory updates: {alert['new_updates_count']} new, {alert['active_comment_periods_count']} active comment periods")
            else:
                logger.info("No new regulatory updates")

        except Exception as e:
            logger.exception(f"Monitor daemon error: {e}")

        await asyncio.sleep(interval_hours * 3600)


# CLI entry point
def main():
    """CLI for testing Federal Register monitor."""
    import argparse

    parser = argparse.ArgumentParser(description="Federal Register HIPAA Monitor")
    parser.add_argument("--check", action="store_true", help="Run one-time check")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--interval", type=int, default=24, help="Check interval (hours)")
    parser.add_argument("--lookback", type=int, default=30, help="Lookback days")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    monitor = FederalRegisterMonitor(lookback_days=args.lookback)

    if args.check:
        # One-time check
        alert = asyncio.run(monitor.generate_compliance_alert())
        print(json.dumps(alert, indent=2))
    elif args.daemon:
        # Run as daemon
        asyncio.run(run_monitor_daemon(args.interval))
    else:
        print("Specify --check or --daemon")


if __name__ == "__main__":
    main()

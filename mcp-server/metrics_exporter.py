#!/usr/bin/env python3
"""
Prometheus Metrics Exporter for MSP Compliance Dashboard

Exposes metrics about compliance controls, incidents, and remediations
for Grafana visualization.

Usage:
    python3 metrics_exporter.py
    # Metrics available at http://localhost:9090/metrics
"""

from prometheus_client import start_http_server, Gauge, Counter, Histogram, Info
from prometheus_client import CollectorRegistry, generate_latest
import time
import json
from pathlib import Path
from datetime import datetime, timedelta
import random

# Create custom registry
registry = CollectorRegistry()

# Metrics definitions
compliance_score = Gauge(
    'msp_compliance_score',
    'Overall compliance score percentage',
    ['client_id'],
    registry=registry
)

control_status = Gauge(
    'msp_control_status',
    'Status of individual compliance controls (1=pass, 0=fail)',
    ['client_id', 'control_id', 'control_name', 'hipaa_control'],
    registry=registry
)

incidents_total = Counter(
    'msp_incidents_total',
    'Total number of incidents detected',
    ['client_id', 'incident_type', 'severity'],
    registry=registry
)

remediations_total = Counter(
    'msp_remediations_total',
    'Total number of remediations attempted',
    ['client_id', 'runbook_id', 'status'],
    registry=registry
)

remediation_duration = Histogram(
    'msp_remediation_duration_seconds',
    'Duration of remediation in seconds',
    ['client_id', 'runbook_id'],
    registry=registry
)

evidence_bundles = Gauge(
    'msp_evidence_bundles',
    'Evidence bundle metadata',
    ['client_id', 'bundle_id', 'generated_at', 'signed', 'worm_url'],
    registry=registry
)

system_health = Gauge(
    'msp_system_health',
    'Health status of MSP components (1=up, 0=down)',
    ['component'],
    registry=registry
)


class MetricsCollector:
    """Collect and expose MSP compliance metrics"""

    def __init__(self):
        self.incident_log = Path("/tmp/msp-demo-incidents.json")
        self.state_dir = Path("/tmp/msp-demo-state")
        self.client_id = "demo-clinic"

        # 8 Core Controls
        self.controls = [
            {
                "id": "164.308(a)(1)(ii)(D)",
                "name": "Information System Activity Review",
                "status": "pass"
            },
            {
                "id": "164.308(a)(7)(ii)(A)",
                "name": "Data Backup Plan",
                "status": "pass"
            },
            {
                "id": "164.310(d)(1)",
                "name": "Device and Media Controls",
                "status": "pass"
            },
            {
                "id": "164.312(a)(1)",
                "name": "Access Control",
                "status": "pass"
            },
            {
                "id": "164.312(a)(2)(iv)",
                "name": "Encryption and Decryption",
                "status": "pass"
            },
            {
                "id": "164.312(b)",
                "name": "Audit Controls",
                "status": "pass"
            },
            {
                "id": "164.312(e)(1)",
                "name": "Transmission Security",
                "status": "pass"
            },
            {
                "id": "164.316(b)(1)",
                "name": "Policies and Procedures",
                "status": "pass"
            }
        ]

    def update_metrics(self):
        """Update all metrics based on current state"""

        # Update control status
        passing_controls = 0
        for control in self.controls:
            # Check if there's an active incident affecting this control
            status_value = 1 if control['status'] == 'pass' else 0
            passing_controls += status_value

            control_status.labels(
                client_id=self.client_id,
                control_id=control['id'],
                control_name=control['name'],
                hipaa_control=control['id']
            ).set(status_value)

        # Update compliance score
        score = (passing_controls / len(self.controls)) * 100
        compliance_score.labels(client_id=self.client_id).set(score)

        # Process incidents from log
        if self.incident_log.exists():
            with open(self.incident_log, 'r') as f:
                for line in f:
                    incident = json.loads(line)

                    # Increment incident counter
                    incidents_total.labels(
                        client_id=self.client_id,
                        incident_type=incident.get('incident_type', 'unknown'),
                        severity=incident.get('level', 'unknown').lower()
                    ).inc(0)  # Don't actually increment, just ensure label exists

        # Simulate some remediations
        for runbook_id in ['RB-BACKUP-001', 'RB-DISK-001', 'RB-SERVICE-001']:
            remediations_total.labels(
                client_id=self.client_id,
                runbook_id=runbook_id,
                status='success'
            ).inc(0)

            # Add remediation duration samples
            remediation_duration.labels(
                client_id=self.client_id,
                runbook_id=runbook_id
            ).observe(random.uniform(30, 120))  # 30-120 seconds

        # Update evidence bundles
        evidence_dir = Path("/var/lib/msp/evidence")
        if evidence_dir.exists():
            for bundle_file in evidence_dir.glob("EB-*.json"):
                try:
                    with open(bundle_file, 'r') as f:
                        bundle = json.load(f)

                    evidence_bundles.labels(
                        client_id=self.client_id,
                        bundle_id=bundle.get('bundle_id', 'unknown'),
                        generated_at=bundle.get('timestamp', 'unknown'),
                        signed='true' if bundle.get('signature') else 'false',
                        worm_url=bundle.get('worm_url', '#')
                    ).set(1)
                except:
                    pass

        # Update system health
        system_health.labels(component='mcp-server').set(1)
        system_health.labels(component='monitoring').set(1)
        system_health.labels(component='evidence-pipeline').set(1)

    def simulate_live_incidents(self):
        """Simulate incidents for demo purposes"""

        # Randomly trigger some incidents
        if random.random() < 0.1:  # 10% chance per update
            incident_types = ['backup_failure', 'disk_full', 'service_crash', 'cert_expiry']
            itype = random.choice(incident_types)

            incidents_total.labels(
                client_id=self.client_id,
                incident_type=itype,
                severity='high'
            ).inc()

            # Also trigger remediation
            runbook_map = {
                'backup_failure': 'RB-BACKUP-001',
                'disk_full': 'RB-DISK-001',
                'service_crash': 'RB-SERVICE-001',
                'cert_expiry': 'RB-CERT-001'
            }

            runbook_id = runbook_map[itype]
            remediations_total.labels(
                client_id=self.client_id,
                runbook_id=runbook_id,
                status='success'
            ).inc()

            duration = random.uniform(30, 120)
            remediation_duration.labels(
                client_id=self.client_id,
                runbook_id=runbook_id
            ).observe(duration)

            print(f"Simulated {itype} -> {runbook_id} (duration: {duration:.1f}s)")


def main():
    """Start metrics exporter"""

    print("🚀 Starting MSP Metrics Exporter...")
    print("   Metrics available at http://localhost:9090/metrics")
    print()

    # Start HTTP server
    start_http_server(9090, registry=registry)

    collector = MetricsCollector()

    # Initial metric collection
    collector.update_metrics()

    print("✅ Metrics exporter running!")
    print("   Configure Prometheus to scrape: http://localhost:9090/metrics")
    print()

    # Update metrics every 10 seconds
    while True:
        time.sleep(10)
        collector.update_metrics()
        collector.simulate_live_incidents()


if __name__ == "__main__":
    main()

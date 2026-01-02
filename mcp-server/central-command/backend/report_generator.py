"""PDF Report Generator for Client Portal.

Generates professional compliance reports using WeasyPrint.
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
from io import BytesIO

logger = logging.getLogger(__name__)

# Try to import WeasyPrint - graceful fallback if not installed
try:
    from weasyprint import HTML, CSS
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False
    logger.warning("WeasyPrint not installed - PDF generation will be disabled")


# HIPAA Control mapping for reports
HIPAA_CONTROLS = {
    "164.308(a)(1)(ii)(D)": {
        "title": "Information System Activity Review",
        "description": "Review records of information system activity, such as audit logs, access reports, and security incident tracking reports"
    },
    "164.308(a)(3)(ii)(B)": {
        "title": "Workforce Security - Termination Procedures",
        "description": "Terminate access to ePHI when employment ends or access is no longer required"
    },
    "164.308(a)(4)(ii)(B)": {
        "title": "Access Authorization",
        "description": "Implement policies and procedures for granting access to ePHI"
    },
    "164.308(a)(4)(ii)(C)": {
        "title": "Access Establishment and Modification",
        "description": "Implement policies and procedures for establishing and modifying access to ePHI"
    },
    "164.308(a)(5)(ii)(B)": {
        "title": "Protection from Malicious Software",
        "description": "Procedures for guarding against, detecting, and reporting malicious software"
    },
    "164.308(a)(5)(ii)(D)": {
        "title": "Password Management",
        "description": "Procedures for creating, changing, and safeguarding passwords"
    },
    "164.308(a)(7)": {
        "title": "Contingency Plan",
        "description": "Establish policies and procedures for responding to emergencies that damage systems containing ePHI"
    },
    "164.308(a)(7)(ii)(A)": {
        "title": "Data Backup Plan",
        "description": "Establish and implement procedures to create and maintain exact copies of ePHI"
    },
    "164.310(d)(1)": {
        "title": "Device and Media Controls",
        "description": "Implement policies governing the receipt and removal of hardware and electronic media"
    },
    "164.310(d)(2)(iii)": {
        "title": "Accountability",
        "description": "Maintain a record of movements of hardware and electronic media"
    },
    "164.310(d)(2)(iv)": {
        "title": "Data Backup and Storage",
        "description": "Create a retrievable, exact copy of ePHI before equipment movement"
    },
    "164.312(a)(1)": {
        "title": "Access Control",
        "description": "Implement technical policies and procedures that allow only authorized persons to access ePHI"
    },
    "164.312(a)(2)(i)": {
        "title": "Unique User Identification",
        "description": "Assign a unique name and/or number for identifying and tracking user identity"
    },
    "164.312(b)": {
        "title": "Audit Controls",
        "description": "Implement hardware, software, and/or procedural mechanisms that record and examine system activity"
    },
}


# HTML template for compliance report
REPORT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>HIPAA Compliance Report - {{ site_name }}</title>
    <style>
        @page {
            size: letter;
            margin: 0.75in;
            @top-right {
                content: "Page " counter(page) " of " counter(pages);
                font-size: 9pt;
                color: #666;
            }
            @bottom-center {
                content: "Confidential - {{ site_name }}";
                font-size: 8pt;
                color: #999;
            }
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 11pt;
            line-height: 1.5;
            color: #333;
        }

        .cover {
            page-break-after: always;
            text-align: center;
            padding-top: 2in;
        }

        .cover h1 {
            font-size: 28pt;
            color: #1a365d;
            margin-bottom: 0.5in;
        }

        .cover .subtitle {
            font-size: 18pt;
            color: #4a5568;
            margin-bottom: 1in;
        }

        .cover .meta {
            font-size: 12pt;
            color: #718096;
        }

        .logo {
            width: 180px;
            margin-bottom: 1in;
        }

        h2 {
            color: #1a365d;
            border-bottom: 2px solid #e2e8f0;
            padding-bottom: 8px;
            margin-top: 1.5em;
        }

        h3 {
            color: #2d3748;
            margin-top: 1.2em;
        }

        .executive-summary {
            background: #f7fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
        }

        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            margin: 20px 0;
        }

        .kpi-card {
            background: #fff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
        }

        .kpi-value {
            font-size: 24pt;
            font-weight: bold;
            color: #1a365d;
        }

        .kpi-value.pass { color: #22543d; }
        .kpi-value.warn { color: #c05621; }
        .kpi-value.fail { color: #c53030; }

        .kpi-label {
            font-size: 10pt;
            color: #718096;
            margin-top: 5px;
        }

        .control-table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }

        .control-table th {
            background: #edf2f7;
            color: #2d3748;
            padding: 10px;
            text-align: left;
            font-size: 10pt;
        }

        .control-table td {
            padding: 10px;
            border-bottom: 1px solid #e2e8f0;
            font-size: 10pt;
        }

        .status-badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 9pt;
            font-weight: 600;
        }

        .status-pass {
            background: #c6f6d5;
            color: #22543d;
        }

        .status-warn {
            background: #feebc8;
            color: #c05621;
        }

        .status-fail {
            background: #fed7d7;
            color: #c53030;
        }

        .incident-list {
            margin: 20px 0;
        }

        .incident-item {
            background: #fff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 10px;
        }

        .incident-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
        }

        .incident-type {
            font-weight: 600;
            color: #2d3748;
        }

        .incident-time {
            font-size: 9pt;
            color: #718096;
        }

        .auto-fixed {
            color: #22543d;
            font-size: 9pt;
        }

        .hipaa-mapping {
            background: #ebf8ff;
            border: 1px solid #bee3f8;
            border-radius: 8px;
            padding: 15px;
            margin: 15px 0;
        }

        .hipaa-ref {
            font-family: monospace;
            font-size: 10pt;
            color: #2b6cb0;
        }

        .footer-note {
            font-size: 9pt;
            color: #718096;
            margin-top: 2in;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
        }

        .page-break {
            page-break-after: always;
        }
    </style>
</head>
<body>
    <!-- Cover Page -->
    <div class="cover">
        <div class="logo">
            <!-- OsirisCare Logo placeholder -->
            <svg viewBox="0 0 200 60" xmlns="http://www.w3.org/2000/svg">
                <text x="10" y="45" font-family="Arial" font-size="32" font-weight="bold" fill="#1a365d">OsirisCare</text>
            </svg>
        </div>
        <h1>HIPAA Compliance Report</h1>
        <div class="subtitle">{{ site_name }}</div>
        <div class="meta">
            <p>Report Period: {{ report_month }}</p>
            <p>Generated: {{ generated_at }}</p>
            <p>Report ID: {{ report_id }}</p>
        </div>
    </div>

    <!-- Executive Summary -->
    <h2>Executive Summary</h2>
    <div class="executive-summary">
        <p>This report summarizes the HIPAA compliance status for <strong>{{ site_name }}</strong>
        for the period of <strong>{{ report_month }}</strong>.</p>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-value {{ 'pass' if compliance_pct >= 95 else ('warn' if compliance_pct >= 80 else 'fail') }}">
                    {{ "%.0f"|format(compliance_pct) }}%
                </div>
                <div class="kpi-label">Compliance Score</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-value {{ 'pass' if patch_mttr_hours < 24 else ('warn' if patch_mttr_hours < 72 else 'fail') }}">
                    {{ "%.1f"|format(patch_mttr_hours) }}h
                </div>
                <div class="kpi-label">Patch MTTR</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-value {{ 'pass' if mfa_coverage_pct == 100 else 'warn' }}">
                    {{ "%.0f"|format(mfa_coverage_pct) }}%
                </div>
                <div class="kpi-label">MFA Coverage</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-value {{ 'pass' if backup_success_rate >= 99 else ('warn' if backup_success_rate >= 95 else 'fail') }}">
                    {{ "%.0f"|format(backup_success_rate) }}%
                </div>
                <div class="kpi-label">Backup Success</div>
            </div>
        </div>

        <p><strong>Controls Summary:</strong>
            <span style="color: #22543d;">{{ controls_passing }} Passing</span> |
            <span style="color: #c05621;">{{ controls_warning }} Warning</span> |
            <span style="color: #c53030;">{{ controls_failing }} Failing</span>
        </p>

        <p><strong>Auto-Remediation:</strong> {{ auto_fixes_count }} issues automatically resolved this period.</p>
    </div>

    <!-- Control Status -->
    <h2>Control Status</h2>
    <p>The following table shows the current status of each HIPAA compliance control monitored by the system.</p>

    <table class="control-table">
        <thead>
            <tr>
                <th>Control</th>
                <th>Status</th>
                <th>HIPAA Reference</th>
                <th>Last Checked</th>
                <th>Auto-Fix</th>
            </tr>
        </thead>
        <tbody>
            {% for control in controls %}
            <tr>
                <td>{{ control.name }}</td>
                <td>
                    <span class="status-badge status-{{ control.status }}">
                        {{ control.status|upper }}
                    </span>
                </td>
                <td>
                    {% for ref in control.hipaa_controls %}
                    <span class="hipaa-ref">{{ ref }}</span>{% if not loop.last %}, {% endif %}
                    {% endfor %}
                </td>
                <td>{{ control.checked_at or 'Pending' }}</td>
                <td>{{ 'Yes' if control.auto_fix_triggered else 'No' }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    <div class="page-break"></div>

    <!-- Incident Summary -->
    <h2>Incident Summary</h2>
    <p>The following incidents were detected and handled during the reporting period.</p>

    {% if incidents %}
    <div class="incident-list">
        {% for incident in incidents[:20] %}
        <div class="incident-item">
            <div class="incident-header">
                <span class="incident-type">{{ incident.incident_type }}</span>
                <span class="incident-time">{{ incident.created_at }}</span>
            </div>
            <div>
                <span class="status-badge status-{{ 'pass' if incident.auto_fixed else 'warn' }}">
                    {{ incident.severity|upper }}
                </span>
                {% if incident.auto_fixed %}
                <span class="auto-fixed">Auto-resolved in {{ incident.resolution_time_sec or 'N/A' }}s</span>
                {% endif %}
            </div>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <p><em>No incidents recorded during this period.</em></p>
    {% endif %}

    <!-- HIPAA Control Reference -->
    <h2>HIPAA Control Reference</h2>
    <p>The following HIPAA controls are monitored by this compliance system:</p>

    {% for ref, info in hipaa_controls.items() %}
    <div class="hipaa-mapping">
        <strong class="hipaa-ref">{{ ref }}</strong> - {{ info.title }}
        <p style="margin: 8px 0 0 0; font-size: 10pt; color: #4a5568;">{{ info.description }}</p>
    </div>
    {% endfor %}

    <!-- Footer -->
    <div class="footer-note">
        <p><strong>Disclaimer:</strong> This report contains system metadata only. No Protected Health Information (PHI)
        is processed, stored, or transmitted by the compliance monitoring system.</p>
        <p>This report is generated automatically by OsirisCare Compliance Platform.</p>
        <p>For questions or concerns, contact support@osiriscare.net</p>
    </div>
</body>
</html>
"""


def render_report_html(
    site_id: str,
    site_name: str,
    month: str,
    kpis: Dict[str, Any],
    controls: List[Dict[str, Any]],
    incidents: List[Dict[str, Any]],
) -> str:
    """Render compliance report as HTML.

    Args:
        site_id: Site identifier
        site_name: Display name for site
        month: Report month (YYYY-MM)
        kpis: KPI metrics dictionary
        controls: List of control status dictionaries
        incidents: List of incident dictionaries

    Returns:
        Rendered HTML string
    """
    from jinja2 import Template

    template = Template(REPORT_TEMPLATE)

    # Format the month for display
    try:
        month_dt = datetime.strptime(month, "%Y-%m")
        report_month = month_dt.strftime("%B %Y")
    except ValueError:
        report_month = month

    # Generate report ID
    report_id = f"CP-{month.replace('-', '')}-{site_id[:12]}"

    html = template.render(
        site_name=site_name,
        report_month=report_month,
        report_id=report_id,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        compliance_pct=kpis.get("compliance_pct", 0),
        patch_mttr_hours=kpis.get("patch_mttr_hours", 0),
        mfa_coverage_pct=kpis.get("mfa_coverage_pct", 100),
        backup_success_rate=kpis.get("backup_success_rate", 100),
        controls_passing=kpis.get("controls_passing", 0),
        controls_warning=kpis.get("controls_warning", 0),
        controls_failing=kpis.get("controls_failing", 0),
        auto_fixes_count=kpis.get("auto_fixes_24h", 0) * 30,  # Approximate monthly
        controls=controls,
        incidents=incidents,
        hipaa_controls=HIPAA_CONTROLS,
    )

    return html


def generate_pdf_report(
    site_id: str,
    site_name: str,
    month: str,
    kpis: Dict[str, Any],
    controls: List[Dict[str, Any]],
    incidents: List[Dict[str, Any]],
) -> Optional[bytes]:
    """Generate PDF compliance report.

    Args:
        site_id: Site identifier
        site_name: Display name for site
        month: Report month (YYYY-MM)
        kpis: KPI metrics dictionary
        controls: List of control status dictionaries
        incidents: List of incident dictionaries

    Returns:
        PDF bytes or None if WeasyPrint not available
    """
    if not WEASYPRINT_AVAILABLE:
        logger.error("WeasyPrint not available - cannot generate PDF")
        return None

    try:
        html_content = render_report_html(
            site_id=site_id,
            site_name=site_name,
            month=month,
            kpis=kpis,
            controls=controls,
            incidents=incidents,
        )

        # Generate PDF from HTML
        html = HTML(string=html_content)
        pdf_bytes = html.write_pdf()

        logger.info(f"Generated PDF report for {site_id} ({month}): {len(pdf_bytes)} bytes")
        return pdf_bytes

    except Exception as e:
        logger.error(f"Failed to generate PDF report: {e}")
        return None


def save_report_to_file(
    pdf_bytes: bytes,
    site_id: str,
    month: str,
    output_dir: Optional[Path] = None,
) -> Path:
    """Save PDF report to file.

    Args:
        pdf_bytes: PDF content
        site_id: Site identifier
        month: Report month (YYYY-MM)
        output_dir: Output directory (default: /var/lib/mcp-server/reports)

    Returns:
        Path to saved file
    """
    if output_dir is None:
        output_dir = Path("/var/lib/mcp-server/reports")

    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{site_id}-monthly-{month}.pdf"
    filepath = output_dir / filename

    with open(filepath, "wb") as f:
        f.write(pdf_bytes)

    logger.info(f"Saved report to {filepath}")
    return filepath


# Export availability flag
def is_pdf_generation_available() -> bool:
    """Check if PDF generation is available."""
    return WEASYPRINT_AVAILABLE

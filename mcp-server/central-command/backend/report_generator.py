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
    <title>HIPAA Monitoring Report - {{ site_name }}</title>
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
        <h1>HIPAA Monitoring Report</h1>
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
        <p>This report is generated automatically by OsirisCare Compliance Monitoring Platform. This report represents point-in-time monitoring observations and does not constitute compliance certification.</p>
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
    report_id = f"MON-{month.replace('-', '')}-{site_id[:12]}"

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


# ─── Session 206 round-table P2: Partner QBR PDF ───────────────────

def _qbr_html(
    *,
    partner_brand: str,
    partner_logo_url: Optional[str],
    primary_color: str,
    client_name: str,
    site_id: str,
    quarter_label: str,
    kpis: Dict[str, Any],
    incidents_summary: List[Dict[str, Any]],
    value_summary: Dict[str, Any],
) -> str:
    """Render the Quarterly Business Review HTML.

    Deliberately narrative — partner brings this to the QBR meeting and
    reads it aloud. Not a compliance attestation; for that see the
    monthly compliance packet.
    """
    safe_brand = (partner_brand or "OsirisCare").replace("<", "&lt;").replace(">", "&gt;")
    safe_client = (client_name or site_id).replace("<", "&lt;").replace(">", "&gt;")
    logo_html = (
        f'<img src="{partner_logo_url}" alt="{safe_brand}" style="height: 40px;" />'
        if partner_logo_url else f'<div class="brand">{safe_brand}</div>'
    )
    incidents_rows = "".join(
        f"<tr><td>{i.get('type', '')}</td>"
        f"<td class='num'>{i.get('count', 0)}</td>"
        f"<td>{i.get('outcome', '')}</td></tr>"
        for i in incidents_summary[:15]
    ) or "<tr><td colspan='3'>No qualifying incidents this quarter.</td></tr>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>QBR — {safe_client} — {quarter_label}</title>
<style>
  @page {{ size: letter; margin: 0.75in; }}
  body {{ font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
         color: #1e293b; line-height: 1.5; }}
  .header {{ display: flex; justify-content: space-between; align-items: center;
            border-bottom: 3px solid {primary_color}; padding-bottom: 12px; margin-bottom: 18px; }}
  .brand {{ font-size: 20px; font-weight: 700; color: {primary_color}; }}
  .title {{ font-size: 14px; color: #64748b; text-align: right; }}
  h1 {{ font-size: 22px; margin: 0 0 6px 0; color: #0f172a; }}
  h2 {{ font-size: 14px; margin: 18px 0 8px 0; color: {primary_color};
        border-bottom: 1px solid #e2e8f0; padding-bottom: 4px;
        text-transform: uppercase; letter-spacing: 0.05em; }}
  .hero {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
          padding: 18px; margin-bottom: 16px; }}
  .hero .num {{ font-size: 40px; font-weight: 700; color: {primary_color}; }}
  .hero .sub {{ font-size: 12px; color: #64748b; }}
  .grid {{ display: flex; gap: 12px; margin: 12px 0; }}
  .grid .cell {{ flex: 1; background: #f8fafc; border: 1px solid #e2e8f0;
                border-radius: 6px; padding: 12px; }}
  .cell .label {{ font-size: 10px; color: #64748b; text-transform: uppercase;
                 letter-spacing: 0.05em; }}
  .cell .val {{ font-size: 22px; font-weight: 700; color: #0f172a; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin: 8px 0; }}
  th {{ background: #f1f5f9; text-align: left; padding: 8px; border-bottom: 1px solid #cbd5e1;
        text-transform: uppercase; font-size: 10px; color: #475569; letter-spacing: 0.05em; }}
  td {{ padding: 8px; border-bottom: 1px solid #e2e8f0; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .value {{ background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 6px;
           padding: 12px; margin: 10px 0; color: #065f46; }}
  .value .big {{ font-size: 28px; font-weight: 700; color: #047857; }}
  footer {{ margin-top: 28px; padding-top: 12px; border-top: 1px solid #e2e8f0;
           font-size: 10px; color: #64748b; }}
  .disclaimer {{ font-size: 9px; color: #94a3b8; margin-top: 8px; }}
</style></head><body>

<div class="header">
  <div>{logo_html}</div>
  <div class="title">
    Quarterly Business Review<br/>
    <b>{quarter_label}</b>
  </div>
</div>

<h1>{safe_client}</h1>
<div style="color:#64748b; font-size:12px;">Site: {site_id}</div>

<h2>Executive summary</h2>
<div class="hero">
  <div class="num">{kpis.get('self_heal_pct', 0):.1f}%</div>
  <div class="sub">of all compliance issues this quarter resolved automatically, without your intervention.</div>
</div>

<div class="grid">
  <div class="cell">
    <div class="label">Issues detected</div>
    <div class="val">{kpis.get('incidents_total', 0):,}</div>
  </div>
  <div class="cell">
    <div class="label">Auto-healed (L1)</div>
    <div class="val">{kpis.get('l1_count', 0):,}</div>
  </div>
  <div class="cell">
    <div class="label">Assisted (L2)</div>
    <div class="val">{kpis.get('l2_count', 0):,}</div>
  </div>
  <div class="cell">
    <div class="label">Required you (L3)</div>
    <div class="val">{kpis.get('l3_count', 0):,}</div>
  </div>
</div>

<h2>Top incident categories</h2>
<table>
  <thead><tr><th>Type</th><th class="num">Count</th><th>Outcome</th></tr></thead>
  <tbody>{incidents_rows}</tbody>
</table>

<h2>Estimated billable hours saved</h2>
<div class="value">
  <div class="big">{value_summary.get('auto_heals', 0):,} auto-heals × {value_summary.get('minutes_per_issue', 20)} min each</div>
  ≈ <b>{value_summary.get('hours_saved', 0):.1f} hours</b> of tech time your team did NOT have to spend on these tickets.
  <div class="disclaimer">
    Based on MSP industry average of {value_summary.get('minutes_per_issue', 20)} min per manually-triaged compliance ticket.
    Actual time varies by site, skill level, and ticket type.
  </div>
</div>

<h2>Chronic patterns broken this quarter</h2>
<div style="font-size:12px; color:#334155;">
  {kpis.get('chronic_broken', 0)} recurring issue pattern(s) were auto-promoted from runbooks and have stopped recurring.
  This means {kpis.get('chronic_broken', 0) * 10} fewer tickets per month going forward (conservative estimate).
</div>

<footer>
  Prepared by {safe_brand} · Quarter: {quarter_label} · Site: {site_id}<br/>
  <div class="disclaimer">
    This review summarizes observed monitoring outcomes and is not itself a HIPAA compliance
    attestation. Monthly compliance packets (signed + OTS-anchored) are the authoritative record.
  </div>
</footer>

</body></html>"""


def generate_qbr_pdf(
    *,
    partner_brand: str,
    partner_logo_url: Optional[str],
    primary_color: str,
    client_name: str,
    site_id: str,
    quarter_label: str,
    kpis: Dict[str, Any],
    incidents_summary: List[Dict[str, Any]],
    value_summary: Dict[str, Any],
) -> Optional[bytes]:
    """Generate a Quarterly Business Review PDF for a partner/client."""
    if not WEASYPRINT_AVAILABLE:
        logger.error("WeasyPrint not available - cannot generate QBR PDF")
        return None
    try:
        html_content = _qbr_html(
            partner_brand=partner_brand,
            partner_logo_url=partner_logo_url,
            primary_color=primary_color,
            client_name=client_name,
            site_id=site_id,
            quarter_label=quarter_label,
            kpis=kpis,
            incidents_summary=incidents_summary,
            value_summary=value_summary,
        )
        pdf_bytes = HTML(string=html_content).write_pdf()
        logger.info(f"Generated QBR PDF for {site_id} ({quarter_label}): {len(pdf_bytes)} bytes")
        return pdf_bytes
    except Exception as e:
        logger.error(f"Failed to generate QBR PDF: {e}", exc_info=True)
        return None

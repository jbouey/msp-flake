"""
PDF report generation using ReportLab.
"""

import io
from datetime import datetime, timezone
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


def generate_compliance_report_pdf(
    site_name: str,
    device_counts: dict,
    compliance_summary: dict,
    device_types: list[dict],
    latest_scan: Optional[dict],
    drifted_devices: list[dict],
) -> bytes:
    """
    Generate a compliance report PDF.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=24,
        spaceAfter=30,
    )
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=14,
        spaceBefore=20,
        spaceAfter=10,
    )

    story = []

    # Title
    story.append(Paragraph(f"Compliance Report - {site_name}", title_style))
    story.append(Paragraph(
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        styles["Normal"],
    ))
    story.append(Spacer(1, 20))

    # Executive Summary
    story.append(Paragraph("Executive Summary", heading_style))
    compliance_rate = compliance_summary.get("compliance_rate", 0)
    status_color = "green" if compliance_rate >= 90 else "orange" if compliance_rate >= 70 else "red"
    summary_text = f"""
    <b>Compliance Rate:</b> <font color="{status_color}">{compliance_rate}%</font><br/>
    <b>Total Devices:</b> {device_counts['total']}<br/>
    <b>Monitored:</b> {device_counts['monitored']}<br/>
    <b>Medical Devices (Excluded):</b> {device_counts['medical']}<br/>
    <b>Devices Needing Attention:</b> {compliance_summary.get('drifted', 0)}
    """
    story.append(Paragraph(summary_text, styles["Normal"]))
    story.append(Spacer(1, 20))

    # Compliance Breakdown
    story.append(Paragraph("Compliance Status Breakdown", heading_style))
    compliance_data = [
        ["Status", "Count", "Percentage"],
        ["Compliant", str(compliance_summary.get("compliant", 0)),
         f"{compliance_summary.get('compliant', 0) / max(compliance_summary.get('total', 1), 1) * 100:.1f}%"],
        ["Drifted", str(compliance_summary.get("drifted", 0)),
         f"{compliance_summary.get('drifted', 0) / max(compliance_summary.get('total', 1), 1) * 100:.1f}%"],
        ["Unknown", str(compliance_summary.get("unknown", 0)),
         f"{compliance_summary.get('unknown', 0) / max(compliance_summary.get('total', 1), 1) * 100:.1f}%"],
        ["Excluded", str(compliance_summary.get("excluded", 0)),
         f"{compliance_summary.get('excluded', 0) / max(compliance_summary.get('total', 1), 1) * 100:.1f}%"],
    ]
    table = Table(compliance_data, colWidths=[2 * inch, 1.5 * inch, 1.5 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f5f5f5")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(table)
    story.append(Spacer(1, 20))

    # Device Types
    story.append(Paragraph("Devices by Type", heading_style))
    type_data = [["Device Type", "Count"]]
    for dt in device_types:
        type_data.append([dt["device_type"].title(), str(dt["count"])])
    if type_data:
        table = Table(type_data, colWidths=[3 * inch, 1.5 * inch])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        story.append(table)
    story.append(Spacer(1, 20))

    # Drifted Devices (needs attention)
    if drifted_devices:
        story.append(Paragraph("Devices Requiring Attention", heading_style))
        drift_data = [["Hostname", "IP Address", "Type", "Status"]]
        for device in drifted_devices[:20]:  # Limit to 20
            drift_data.append([
                device.get("hostname") or "Unknown",
                device.get("ip_address", ""),
                device.get("device_type", "unknown").title(),
                device.get("compliance_status", "unknown"),
            ])
        table = Table(drift_data, colWidths=[2 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        story.append(table)

    # Last Scan Info
    if latest_scan:
        story.append(Spacer(1, 20))
        story.append(Paragraph("Last Network Scan", heading_style))
        scan_text = f"""
        <b>Scan ID:</b> {latest_scan.get('id', 'N/A')}<br/>
        <b>Started:</b> {latest_scan.get('started_at', 'N/A')}<br/>
        <b>Completed:</b> {latest_scan.get('completed_at', 'N/A')}<br/>
        <b>Devices Found:</b> {latest_scan.get('devices_found', 0)}<br/>
        <b>New Devices:</b> {latest_scan.get('new_devices', 0)}
        """
        story.append(Paragraph(scan_text, styles["Normal"]))

    # Footer
    story.append(Spacer(1, 40))
    story.append(Paragraph(
        "<i>This report was generated by MSP Compliance Platform. "
        "Medical devices are excluded by default for patient safety.</i>",
        styles["Normal"],
    ))

    doc.build(story)
    return buffer.getvalue()


def generate_inventory_report_pdf(
    site_name: str,
    devices: list[dict],
    device_counts: dict,
    device_types: list[dict],
) -> bytes:
    """
    Generate a device inventory PDF.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=24,
        spaceAfter=30,
    )
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=14,
        spaceBefore=20,
        spaceAfter=10,
    )

    story = []

    # Title
    story.append(Paragraph(f"Device Inventory - {site_name}", title_style))
    story.append(Paragraph(
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        styles["Normal"],
    ))
    story.append(Spacer(1, 20))

    # Summary
    story.append(Paragraph("Summary", heading_style))
    summary_text = f"""
    <b>Total Devices:</b> {device_counts['total']}<br/>
    <b>Monitored:</b> {device_counts['monitored']}<br/>
    <b>Discovered:</b> {device_counts['discovered']}<br/>
    <b>Excluded:</b> {device_counts['excluded']}<br/>
    <b>Medical Devices:</b> {device_counts['medical']}
    """
    story.append(Paragraph(summary_text, styles["Normal"]))
    story.append(Spacer(1, 20))

    # Device Table
    story.append(Paragraph("Device List", heading_style))
    device_data = [["Hostname", "IP Address", "Type", "Status", "Compliance"]]
    for device in devices[:100]:  # Limit to 100 for PDF
        device_data.append([
            (device.get("hostname") or "Unknown")[:25],
            device.get("ip_address", "")[:15],
            device.get("device_type", "unknown")[:12],
            device.get("status", "unknown")[:12],
            device.get("compliance_status", "unknown")[:12],
        ])

    table = Table(device_data, colWidths=[1.8 * inch, 1.2 * inch, 1 * inch, 1 * inch, 1 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    story.append(table)

    if len(devices) > 100:
        story.append(Spacer(1, 10))
        story.append(Paragraph(
            f"<i>Showing first 100 of {len(devices)} devices. Export CSV for complete list.</i>",
            styles["Normal"],
        ))

    # Footer
    story.append(Spacer(1, 40))
    story.append(Paragraph(
        "<i>This inventory was generated by MSP Compliance Platform.</i>",
        styles["Normal"],
    ))

    doc.build(story)
    return buffer.getvalue()

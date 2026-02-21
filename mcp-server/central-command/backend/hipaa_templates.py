"""
HIPAA Compliance Templates.

SRA question bank, policy document templates, incident response templates,
physical safeguard checklist items, and gap analysis questionnaire.
"""

# =============================================================================
# SRA QUESTION BANK — 40 questions across 3 categories
# =============================================================================

SRA_QUESTIONS = [
    # --- Administrative Safeguards (15 questions) ---
    {
        "key": "admin_risk_analysis",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(1)(ii)(A)",
        "text": "Has your organization conducted an accurate and thorough assessment of potential risks and vulnerabilities to ePHI?",
    },
    {
        "key": "admin_risk_mgmt",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(1)(ii)(B)",
        "text": "Are security measures in place that are sufficient to reduce identified risks to a reasonable and appropriate level?",
    },
    {
        "key": "admin_sanction_policy",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(1)(ii)(C)",
        "text": "Does the organization have a sanction policy for workforce members who fail to comply with security policies?",
    },
    {
        "key": "admin_info_system_review",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(1)(ii)(D)",
        "text": "Are information system activity reviews (audit logs, access reports) conducted on a regular basis?",
    },
    {
        "key": "admin_workforce_security",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(3)",
        "text": "Are policies and procedures in place to ensure workforce members have appropriate access to ePHI?",
    },
    {
        "key": "admin_access_authorization",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(4)",
        "text": "Are there policies for granting access to ePHI, including role-based access controls?",
    },
    {
        "key": "admin_security_awareness",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(5)",
        "text": "Is a security awareness and training program in place for all workforce members, including management?",
    },
    {
        "key": "admin_security_incident",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(6)",
        "text": "Are there policies and procedures to address security incidents, including response and reporting?",
    },
    {
        "key": "admin_contingency_plan",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(7)",
        "text": "Is a contingency plan established to respond to emergencies or other events that damage systems containing ePHI?",
    },
    {
        "key": "admin_evaluation",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(8)",
        "text": "Are periodic technical and non-technical evaluations performed to assess compliance with security policies?",
    },
    {
        "key": "admin_baa",
        "category": "administrative",
        "hipaa_reference": "164.308(b)(1)",
        "text": "Are Business Associate Agreements in place with all vendors and partners who access, maintain, or transmit ePHI?",
    },
    {
        "key": "admin_privacy_officer",
        "category": "administrative",
        "hipaa_reference": "164.530(a)(1)",
        "text": "Has a Privacy Officer been designated who is responsible for developing and implementing privacy policies?",
    },
    {
        "key": "admin_security_officer",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(2)",
        "text": "Has a Security Officer been designated who is responsible for developing and implementing security policies?",
    },
    {
        "key": "admin_termination",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(3)(ii)(C)",
        "text": "Are there termination procedures to revoke access to ePHI when a workforce member leaves the organization?",
    },
    {
        "key": "admin_password_mgmt",
        "category": "administrative",
        "hipaa_reference": "164.308(a)(5)(ii)(D)",
        "text": "Are procedures in place for creating, changing, and safeguarding passwords?",
    },

    # --- Physical Safeguards (10 questions) ---
    {
        "key": "phys_facility_access",
        "category": "physical",
        "hipaa_reference": "164.310(a)(1)",
        "text": "Are facility access controls in place to limit physical access to electronic information systems and the facilities in which they are housed?",
    },
    {
        "key": "phys_contingency_ops",
        "category": "physical",
        "hipaa_reference": "164.310(a)(2)(i)",
        "text": "Are there procedures to allow facility access in support of restoration of lost data under the disaster recovery plan?",
    },
    {
        "key": "phys_facility_security",
        "category": "physical",
        "hipaa_reference": "164.310(a)(2)(ii)",
        "text": "Are safeguards in place to protect the facility and equipment from unauthorized physical access, tampering, and theft?",
    },
    {
        "key": "phys_access_control",
        "category": "physical",
        "hipaa_reference": "164.310(a)(2)(iii)",
        "text": "Are procedures in place to control and validate a person's access to facilities based on their role or function?",
    },
    {
        "key": "phys_maintenance_records",
        "category": "physical",
        "hipaa_reference": "164.310(a)(2)(iv)",
        "text": "Are maintenance records for physical security modifications (locks, keys, card readers) documented and maintained?",
    },
    {
        "key": "phys_workstation_use",
        "category": "physical",
        "hipaa_reference": "164.310(b)",
        "text": "Are policies in place that specify proper functions, manner of use, and physical attributes of workstation surroundings for accessing ePHI?",
    },
    {
        "key": "phys_workstation_security",
        "category": "physical",
        "hipaa_reference": "164.310(c)",
        "text": "Are physical safeguards in place for all workstations that access ePHI to restrict access to authorized users?",
    },
    {
        "key": "phys_device_disposal",
        "category": "physical",
        "hipaa_reference": "164.310(d)(2)(i)",
        "text": "Are procedures in place to address the final disposition of ePHI and/or hardware or electronic media on which it is stored?",
    },
    {
        "key": "phys_media_reuse",
        "category": "physical",
        "hipaa_reference": "164.310(d)(2)(ii)",
        "text": "Are procedures in place for removal of ePHI from electronic media before the media is made available for re-use?",
    },
    {
        "key": "phys_media_movement",
        "category": "physical",
        "hipaa_reference": "164.310(d)(2)(iii)",
        "text": "Is a record of movements of hardware and electronic media maintained, including the person responsible?",
    },

    # --- Technical Safeguards (15 questions) ---
    {
        "key": "tech_unique_user_id",
        "category": "technical",
        "hipaa_reference": "164.312(a)(2)(i)",
        "text": "Does each workforce member have a unique user identifier for tracking access to ePHI?",
    },
    {
        "key": "tech_emergency_access",
        "category": "technical",
        "hipaa_reference": "164.312(a)(2)(ii)",
        "text": "Are there procedures for obtaining necessary ePHI during an emergency?",
    },
    {
        "key": "tech_auto_logoff",
        "category": "technical",
        "hipaa_reference": "164.312(a)(2)(iii)",
        "text": "Do electronic systems that access ePHI automatically terminate sessions after a predetermined time of inactivity?",
    },
    {
        "key": "tech_encryption_at_rest",
        "category": "technical",
        "hipaa_reference": "164.312(a)(2)(iv)",
        "text": "Is a mechanism in place to encrypt ePHI at rest?",
    },
    {
        "key": "tech_audit_controls",
        "category": "technical",
        "hipaa_reference": "164.312(b)",
        "text": "Are hardware, software, and/or procedural mechanisms in place to record and examine activity in systems that contain or use ePHI?",
    },
    {
        "key": "tech_integrity_controls",
        "category": "technical",
        "hipaa_reference": "164.312(c)(1)",
        "text": "Are policies and procedures in place to protect ePHI from improper alteration or destruction?",
    },
    {
        "key": "tech_integrity_mechanism",
        "category": "technical",
        "hipaa_reference": "164.312(c)(2)",
        "text": "Are electronic mechanisms employed to corroborate that ePHI has not been altered or destroyed in an unauthorized manner?",
    },
    {
        "key": "tech_person_auth",
        "category": "technical",
        "hipaa_reference": "164.312(d)",
        "text": "Are procedures in place to verify that a person or entity seeking access to ePHI is the one claimed?",
    },
    {
        "key": "tech_transmission_security",
        "category": "technical",
        "hipaa_reference": "164.312(e)(1)",
        "text": "Are technical security measures in place to guard against unauthorized access to ePHI being transmitted over a network?",
    },
    {
        "key": "tech_encryption_transit",
        "category": "technical",
        "hipaa_reference": "164.312(e)(2)(ii)",
        "text": "Is a mechanism in place to encrypt ePHI whenever it is transmitted electronically?",
    },
    {
        "key": "tech_antivirus",
        "category": "technical",
        "hipaa_reference": "164.308(a)(5)(ii)(B)",
        "text": "Are there procedures for guarding against, detecting, and reporting malicious software?",
    },
    {
        "key": "tech_login_monitoring",
        "category": "technical",
        "hipaa_reference": "164.308(a)(5)(ii)(C)",
        "text": "Are there procedures for monitoring login attempts and reporting discrepancies?",
    },
    {
        "key": "tech_data_backup",
        "category": "technical",
        "hipaa_reference": "164.308(a)(7)(ii)(A)",
        "text": "Are procedures in place to create and maintain retrievable exact copies of ePHI?",
    },
    {
        "key": "tech_disaster_recovery",
        "category": "technical",
        "hipaa_reference": "164.308(a)(7)(ii)(B)",
        "text": "Are procedures in place to restore lost data from backups?",
    },
    {
        "key": "tech_emergency_mode",
        "category": "technical",
        "hipaa_reference": "164.308(a)(7)(ii)(C)",
        "text": "Are procedures in place to enable continuation of critical business processes for protection of ePHI while operating in emergency mode?",
    },
]


# =============================================================================
# POLICY TEMPLATES — 8 core HIPAA policies
# =============================================================================

POLICY_TEMPLATES = {
    "access_control": {
        "title": "Access Control Policy",
        "hipaa_references": ["164.312(a)"],
        "content": """# Access Control Policy

**Organization:** {{ORG_NAME}}
**Effective Date:** {{EFFECTIVE_DATE}}
**Security Officer:** {{SECURITY_OFFICER}}
**Version:** 1.0

## Purpose

This policy establishes requirements for controlling access to electronic protected health information (ePHI) within {{ORG_NAME}}.

## Scope

This policy applies to all workforce members, including employees, contractors, and volunteers who access information systems containing ePHI.

## Policy

### Unique User Identification
- Each user must be assigned a unique identifier for system access.
- Shared accounts are prohibited.

### Emergency Access Procedure
- Emergency access procedures are documented and tested annually.
- Emergency access must be logged and reviewed within 24 hours.

### Automatic Logoff
- Systems accessing ePHI must automatically log off after 15 minutes of inactivity.

### Encryption
- ePHI stored on workstations and portable devices must be encrypted using AES-256 or equivalent.

### Access Authorization
- Access to ePHI is granted based on job function and minimum necessary standard.
- Access requests must be approved by the department supervisor and Security Officer.

### Access Termination
- Access must be revoked within 24 hours of workforce member termination.
- Access rights must be reviewed when a workforce member changes roles.

## Enforcement

Violations of this policy may result in disciplinary action up to and including termination.

## Review

This policy will be reviewed annually by the Security Officer.
""",
    },
    "audit_controls": {
        "title": "Audit Controls Policy",
        "hipaa_references": ["164.312(b)"],
        "content": """# Audit Controls Policy

**Organization:** {{ORG_NAME}}
**Effective Date:** {{EFFECTIVE_DATE}}
**Security Officer:** {{SECURITY_OFFICER}}
**Version:** 1.0

## Purpose

This policy establishes requirements for recording and examining access and activity in information systems containing ePHI.

## Policy

### Audit Logging
- All systems containing ePHI must have audit logging enabled.
- Logs must capture: user ID, timestamp, action performed, data accessed.
- Logs must be retained for a minimum of 6 years.

### Log Review
- Audit logs must be reviewed at least weekly.
- Unusual or suspicious activity must be investigated and documented.

### Log Protection
- Audit logs must be protected from unauthorized modification or deletion.
- Logs must be stored in a separate location from the systems they monitor.

### Automated Monitoring
- Automated alerting must be configured for failed login attempts, privilege escalation, and bulk data access.

## Review

This policy will be reviewed annually by the Security Officer.
""",
    },
    "integrity_controls": {
        "title": "Integrity Controls Policy",
        "hipaa_references": ["164.312(c)"],
        "content": """# Integrity Controls Policy

**Organization:** {{ORG_NAME}}
**Effective Date:** {{EFFECTIVE_DATE}}
**Security Officer:** {{SECURITY_OFFICER}}
**Version:** 1.0

## Purpose

This policy establishes requirements to protect ePHI from improper alteration or destruction.

## Policy

### Data Integrity Mechanisms
- Systems must employ checksums or digital signatures to verify data integrity.
- Database transactions must use ACID-compliant systems.

### Change Management
- All changes to systems handling ePHI must follow a documented change management process.
- Changes must be tested in a non-production environment before deployment.

### Backup Verification
- Backup integrity must be verified through periodic test restorations.
- Backup checksums must be validated automatically.

## Review

This policy will be reviewed annually by the Security Officer.
""",
    },
    "transmission_security": {
        "title": "Transmission Security Policy",
        "hipaa_references": ["164.312(e)"],
        "content": """# Transmission Security Policy

**Organization:** {{ORG_NAME}}
**Effective Date:** {{EFFECTIVE_DATE}}
**Security Officer:** {{SECURITY_OFFICER}}
**Version:** 1.0

## Purpose

This policy establishes requirements for protecting ePHI during electronic transmission.

## Policy

### Encryption in Transit
- All ePHI transmitted over public networks must be encrypted using TLS 1.2 or higher.
- Unencrypted email transmission of ePHI is prohibited.

### Integrity Controls
- Electronic transmissions of ePHI must include integrity verification mechanisms.

### VPN Requirements
- Remote access to internal systems containing ePHI must use an approved VPN.

### Wireless Networks
- Wireless networks used to access ePHI must use WPA3 or WPA2 enterprise encryption.
- Guest networks must be segregated from clinical networks.

## Review

This policy will be reviewed annually by the Security Officer.
""",
    },
    "facility_access": {
        "title": "Facility Access Controls Policy",
        "hipaa_references": ["164.310(a)"],
        "content": """# Facility Access Controls Policy

**Organization:** {{ORG_NAME}}
**Effective Date:** {{EFFECTIVE_DATE}}
**Security Officer:** {{SECURITY_OFFICER}}
**Version:** 1.0

## Purpose

This policy establishes requirements for controlling physical access to facilities housing ePHI systems.

## Policy

### Access Controls
- Server rooms and network closets must be locked at all times.
- Access to restricted areas must be limited to authorized personnel.
- Visitor access must be logged and escorted.

### Facility Security
- Exterior doors must be locked during non-business hours.
- Security cameras should monitor entry points to restricted areas.

### Maintenance Records
- All modifications to physical security (lock changes, key issuance) must be documented.

### Emergency Access
- Emergency access procedures must be documented for fire, flood, and other emergencies.

## Review

This policy will be reviewed annually by the Security Officer.
""",
    },
    "workstation_security": {
        "title": "Workstation Security Policy",
        "hipaa_references": ["164.310(b)", "164.310(c)"],
        "content": """# Workstation Security Policy

**Organization:** {{ORG_NAME}}
**Effective Date:** {{EFFECTIVE_DATE}}
**Security Officer:** {{SECURITY_OFFICER}}
**Version:** 1.0

## Purpose

This policy establishes requirements for workstation use and physical security.

## Policy

### Workstation Use
- Workstations accessing ePHI must be positioned to prevent unauthorized viewing.
- Workforce members must lock screens when stepping away.
- Personal devices may not be used to access ePHI without approval.

### Physical Security
- Workstations must be secured to prevent theft (cable locks for laptops).
- Portable devices containing ePHI must be encrypted.

### Software Requirements
- All workstations must run approved operating systems with current security patches.
- Antivirus software must be installed and kept current.

## Review

This policy will be reviewed annually by the Security Officer.
""",
    },
    "device_media_controls": {
        "title": "Device and Media Controls Policy",
        "hipaa_references": ["164.310(d)"],
        "content": """# Device and Media Controls Policy

**Organization:** {{ORG_NAME}}
**Effective Date:** {{EFFECTIVE_DATE}}
**Security Officer:** {{SECURITY_OFFICER}}
**Version:** 1.0

## Purpose

This policy governs receipt, removal, movement, and disposal of hardware and electronic media containing ePHI.

## Policy

### Disposal
- All ePHI must be securely erased before disposing of electronic media.
- Physical destruction (shredding, degaussing) is required for media that cannot be wiped.
- Disposal must be documented with a certificate of destruction.

### Media Re-Use
- Electronic media must be sanitized using NIST 800-88 guidelines before re-use.

### Hardware Movement
- All hardware containing ePHI must be tracked with an inventory log.
- Movement of devices between locations must be authorized and recorded.

### Backups
- Retrievable copies of ePHI must be created before equipment is moved for maintenance.

## Review

This policy will be reviewed annually by the Security Officer.
""",
    },
    "administrative_safeguards": {
        "title": "Administrative Safeguards Policy",
        "hipaa_references": ["164.308"],
        "content": """# Administrative Safeguards Policy

**Organization:** {{ORG_NAME}}
**Effective Date:** {{EFFECTIVE_DATE}}
**Security Officer:** {{SECURITY_OFFICER}}
**Version:** 1.0

## Purpose

This policy establishes the administrative framework for HIPAA security compliance at {{ORG_NAME}}.

## Policy

### Security Management
- A formal risk analysis must be conducted annually.
- Risk mitigation plans must be developed for identified vulnerabilities.

### Workforce Security
- Background checks must be conducted for workforce members with access to ePHI.
- Access authorization must follow the minimum necessary standard.

### Security Awareness
- All workforce members must complete HIPAA security training within 30 days of hire.
- Annual refresher training is required for all workforce members.

### Security Incidents
- All suspected security incidents must be reported to the Security Officer immediately.
- The incident response plan must be followed for all confirmed incidents.

### Business Associate Management
- BAAs must be executed before any business associate accesses ePHI.
- BAA compliance must be reviewed annually.

## Review

This policy will be reviewed annually by the Security Officer.
""",
    },
}


# =============================================================================
# INCIDENT RESPONSE PLAN TEMPLATE
# =============================================================================

IR_PLAN_TEMPLATE = """# Incident Response Plan

**Organization:** {{ORG_NAME}}
**Security Officer:** {{SECURITY_OFFICER}}
**Privacy Officer:** {{PRIVACY_OFFICER}}
**Version:** 1.0

## 1. Purpose

This plan establishes procedures for identifying, responding to, and mitigating security incidents involving ePHI at {{ORG_NAME}}.

## 2. Incident Response Team

| Role | Name | Contact |
|------|------|---------|
| Security Officer | {{SECURITY_OFFICER}} | |
| Privacy Officer | {{PRIVACY_OFFICER}} | |
| IT Support | | |
| Legal Counsel | | |

## 3. Incident Categories

- **Level 1 (Low):** Failed login attempts, minor policy violations
- **Level 2 (Medium):** Unauthorized access attempts, malware detection
- **Level 3 (High):** Confirmed unauthorized access to ePHI, ransomware
- **Level 4 (Critical):** Confirmed breach affecting 500+ individuals

## 4. Response Procedures

### Detection
1. Monitor security alerts and audit logs
2. Accept reports from workforce members
3. Review automated compliance alerts from OsirisCare

### Containment
1. Isolate affected systems immediately
2. Preserve evidence (logs, screenshots)
3. Notify Security Officer within 1 hour

### Eradication
1. Remove malware or unauthorized access
2. Patch exploited vulnerabilities
3. Reset compromised credentials

### Recovery
1. Restore systems from clean backups
2. Verify system integrity before returning to production
3. Monitor for recurrence

### Post-Incident
1. Document root cause and corrective actions
2. Update security policies as needed
3. Conduct lessons-learned review

## 5. Breach Notification

Per 45 CFR 164.404-164.410:
- Affected individuals must be notified within 60 days of discovery
- HHS must be notified annually (under 500 affected) or within 60 days (500+)
- Media notification required for breaches affecting 500+ in a state/jurisdiction

## 6. Testing

This plan must be tested at least annually through tabletop exercises.
"""


# =============================================================================
# PHYSICAL SAFEGUARDS CHECKLIST ITEMS
# =============================================================================

PHYSICAL_SAFEGUARD_ITEMS = [
    # Facility Access Controls
    {"category": "facility_access", "item_key": "exterior_locks", "description": "All exterior doors have functioning locks", "hipaa_reference": "164.310(a)(1)"},
    {"category": "facility_access", "item_key": "badge_access", "description": "Badge or key card access system for restricted areas", "hipaa_reference": "164.310(a)(2)(iii)"},
    {"category": "facility_access", "item_key": "server_room_lock", "description": "Server room/network closet has separate lock", "hipaa_reference": "164.310(a)(2)(ii)"},
    {"category": "facility_access", "item_key": "visitor_log", "description": "Visitor sign-in log maintained at reception", "hipaa_reference": "164.310(a)(2)(iii)"},
    {"category": "facility_access", "item_key": "security_cameras", "description": "Security cameras at entry points", "hipaa_reference": "164.310(a)(2)(ii)"},
    {"category": "facility_access", "item_key": "alarm_system", "description": "Alarm system for after-hours intrusion detection", "hipaa_reference": "164.310(a)(2)(ii)"},
    {"category": "facility_access", "item_key": "key_management", "description": "Key/badge inventory with issuance tracking", "hipaa_reference": "164.310(a)(2)(iv)"},

    # Workstation Use
    {"category": "workstation_use", "item_key": "screen_positioning", "description": "Workstation screens positioned away from public view", "hipaa_reference": "164.310(b)"},
    {"category": "workstation_use", "item_key": "privacy_screens", "description": "Privacy screens on monitors in public areas", "hipaa_reference": "164.310(b)"},
    {"category": "workstation_use", "item_key": "clean_desk", "description": "Clean desk policy enforced (no PHI left visible)", "hipaa_reference": "164.310(b)"},
    {"category": "workstation_use", "item_key": "auto_lock", "description": "Workstations auto-lock after inactivity", "hipaa_reference": "164.310(b)"},

    # Workstation Security
    {"category": "workstation_security", "item_key": "cable_locks", "description": "Laptop cable locks or secure docking stations", "hipaa_reference": "164.310(c)"},
    {"category": "workstation_security", "item_key": "encrypted_drives", "description": "Full-disk encryption on all workstations", "hipaa_reference": "164.310(c)"},
    {"category": "workstation_security", "item_key": "usb_controls", "description": "USB port controls to prevent unauthorized data transfer", "hipaa_reference": "164.310(c)"},

    # Device and Media Controls
    {"category": "device_media", "item_key": "media_disposal", "description": "Secure media disposal procedure (shredding, degaussing)", "hipaa_reference": "164.310(d)(2)(i)"},
    {"category": "device_media", "item_key": "device_inventory", "description": "Hardware inventory maintained and current", "hipaa_reference": "164.310(d)(1)"},
    {"category": "device_media", "item_key": "media_sanitization", "description": "Media sanitization before re-use (NIST 800-88)", "hipaa_reference": "164.310(d)(2)(ii)"},
    {"category": "device_media", "item_key": "movement_log", "description": "Equipment movement log for devices with ePHI", "hipaa_reference": "164.310(d)(2)(iii)"},
    {"category": "device_media", "item_key": "backup_before_move", "description": "Data backed up before hardware maintenance/movement", "hipaa_reference": "164.310(d)(2)(iv)"},
]


# =============================================================================
# GAP ANALYSIS QUESTIONNAIRE — comprehensive HIPAA maturity assessment
# =============================================================================

GAP_ANALYSIS_QUESTIONS = [
    # Administrative
    {"section": "administrative", "question_key": "gap_risk_analysis", "hipaa_reference": "164.308(a)(1)(ii)(A)",
     "text": "Has a comprehensive risk analysis been performed within the last 12 months?"},
    {"section": "administrative", "question_key": "gap_risk_management", "hipaa_reference": "164.308(a)(1)(ii)(B)",
     "text": "Are risk mitigation measures documented and tracked to completion?"},
    {"section": "administrative", "question_key": "gap_sanction_policy", "hipaa_reference": "164.308(a)(1)(ii)(C)",
     "text": "Is there a documented sanction policy for HIPAA violations?"},
    {"section": "administrative", "question_key": "gap_review_activity", "hipaa_reference": "164.308(a)(1)(ii)(D)",
     "text": "Are information system activity reviews performed regularly?"},
    {"section": "administrative", "question_key": "gap_security_officer", "hipaa_reference": "164.308(a)(2)",
     "text": "Has a Security Officer been formally designated?"},
    {"section": "administrative", "question_key": "gap_workforce_access", "hipaa_reference": "164.308(a)(3)",
     "text": "Are workforce access controls based on job function and minimum necessary?"},
    {"section": "administrative", "question_key": "gap_access_mgmt", "hipaa_reference": "164.308(a)(4)",
     "text": "Is there a formal access management process for granting and revoking access?"},
    {"section": "administrative", "question_key": "gap_training", "hipaa_reference": "164.308(a)(5)",
     "text": "Do all workforce members receive HIPAA training upon hire and annually?"},
    {"section": "administrative", "question_key": "gap_incident_response", "hipaa_reference": "164.308(a)(6)",
     "text": "Is there a documented incident response plan that has been tested?"},
    {"section": "administrative", "question_key": "gap_contingency", "hipaa_reference": "164.308(a)(7)",
     "text": "Is a contingency plan in place with data backup, disaster recovery, and emergency mode procedures?"},
    {"section": "administrative", "question_key": "gap_evaluation", "hipaa_reference": "164.308(a)(8)",
     "text": "Are periodic evaluations of security policies and procedures conducted?"},
    {"section": "administrative", "question_key": "gap_baa_mgmt", "hipaa_reference": "164.308(b)",
     "text": "Are Business Associate Agreements in place for all applicable vendors?"},

    # Physical
    {"section": "physical", "question_key": "gap_facility_access", "hipaa_reference": "164.310(a)(1)",
     "text": "Are facility access controls implemented and documented?"},
    {"section": "physical", "question_key": "gap_workstation_use", "hipaa_reference": "164.310(b)",
     "text": "Are workstation use policies documented and enforced?"},
    {"section": "physical", "question_key": "gap_workstation_security", "hipaa_reference": "164.310(c)",
     "text": "Are workstations physically secured to prevent unauthorized access?"},
    {"section": "physical", "question_key": "gap_device_media", "hipaa_reference": "164.310(d)",
     "text": "Are device and media controls in place for disposal, re-use, and movement?"},

    # Technical
    {"section": "technical", "question_key": "gap_access_control_tech", "hipaa_reference": "164.312(a)",
     "text": "Are technical access controls (unique IDs, auto-logoff, encryption) implemented?"},
    {"section": "technical", "question_key": "gap_audit_controls", "hipaa_reference": "164.312(b)",
     "text": "Are audit controls in place to record and examine system activity?"},
    {"section": "technical", "question_key": "gap_integrity", "hipaa_reference": "164.312(c)",
     "text": "Are integrity controls in place to protect ePHI from unauthorized alteration?"},
    {"section": "technical", "question_key": "gap_authentication", "hipaa_reference": "164.312(d)",
     "text": "Are person or entity authentication mechanisms in place?"},
    {"section": "technical", "question_key": "gap_transmission", "hipaa_reference": "164.312(e)",
     "text": "Are transmission security controls (encryption, integrity checks) in place?"},

    # Organizational
    {"section": "organizational", "question_key": "gap_privacy_officer", "hipaa_reference": "164.530(a)(1)",
     "text": "Has a Privacy Officer been formally designated?"},
    {"section": "organizational", "question_key": "gap_notice_practices", "hipaa_reference": "164.520",
     "text": "Is a Notice of Privacy Practices provided to patients and posted in the facility?"},
    {"section": "organizational", "question_key": "gap_patient_rights", "hipaa_reference": "164.524",
     "text": "Are procedures in place for patients to access and amend their records?"},
    {"section": "organizational", "question_key": "gap_accounting_disclosures", "hipaa_reference": "164.528",
     "text": "Is an accounting of disclosures maintained for PHI?"},
    {"section": "organizational", "question_key": "gap_breach_notification", "hipaa_reference": "164.404",
     "text": "Are breach notification procedures documented and ready to execute?"},
]

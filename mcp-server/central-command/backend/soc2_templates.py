"""
SOC 2 Type II Compliance Templates.

Assessment question bank and policy document templates based on
AICPA Trust Service Criteria (TSC).
"""

# =============================================================================
# SOC 2 ASSESSMENT QUESTIONS — 30 questions across 5 categories
# =============================================================================

SOC2_ASSESSMENT_QUESTIONS = [
    # --- Common Criteria (CC) — 15 questions ---

    # CC1: Control Environment
    {
        "key": "cc1_board_oversight",
        "category": "common_criteria",
        "soc2_reference": "CC1.1",
        "text": "Does the board of directors or equivalent governing body demonstrate independence and oversight of the design and operation of the system of internal control?",
    },
    {
        "key": "cc1_management_philosophy",
        "category": "common_criteria",
        "soc2_reference": "CC1.2",
        "text": "Does management establish structures, reporting lines, and appropriate authorities and responsibilities in pursuit of objectives?",
    },
    {
        "key": "cc1_org_structure",
        "category": "common_criteria",
        "soc2_reference": "CC1.3",
        "text": "Does the organization demonstrate a commitment to attract, develop, and retain competent individuals in alignment with objectives?",
    },

    # CC2: Communication and Information
    {
        "key": "cc2_internal_communication",
        "category": "common_criteria",
        "soc2_reference": "CC2.1",
        "text": "Does the organization obtain or generate and use relevant, quality information to support the functioning of internal controls?",
    },
    {
        "key": "cc2_external_communication",
        "category": "common_criteria",
        "soc2_reference": "CC2.2",
        "text": "Does the organization communicate with external parties regarding matters affecting the functioning of internal controls, including commitments in customer agreements?",
    },

    # CC3: Risk Assessment
    {
        "key": "cc3_risk_identification",
        "category": "common_criteria",
        "soc2_reference": "CC3.1",
        "text": "Does the organization specify objectives with sufficient clarity to enable the identification and assessment of risks related to those objectives?",
    },
    {
        "key": "cc3_fraud_risk",
        "category": "common_criteria",
        "soc2_reference": "CC3.3",
        "text": "Does the organization consider the potential for fraud in assessing risks to the achievement of objectives?",
    },

    # CC4: Monitoring Activities
    {
        "key": "cc4_ongoing_monitoring",
        "category": "common_criteria",
        "soc2_reference": "CC4.1",
        "text": "Does the organization select, develop, and perform ongoing and/or separate evaluations to ascertain whether the components of internal control are present and functioning?",
    },
    {
        "key": "cc4_deficiency_evaluation",
        "category": "common_criteria",
        "soc2_reference": "CC4.2",
        "text": "Does the organization evaluate and communicate internal control deficiencies in a timely manner to those parties responsible for taking corrective action?",
    },

    # CC5: Control Activities
    {
        "key": "cc5_technology_controls",
        "category": "common_criteria",
        "soc2_reference": "CC5.2",
        "text": "Does the organization select and develop general control activities over technology to support the achievement of objectives?",
    },
    {
        "key": "cc5_policies_procedures",
        "category": "common_criteria",
        "soc2_reference": "CC5.3",
        "text": "Does the organization deploy control activities through policies that establish what is expected and procedures that put policies into action?",
    },

    # CC6: Logical and Physical Access Controls
    {
        "key": "cc6_access_restrictions",
        "category": "common_criteria",
        "soc2_reference": "CC6.1",
        "text": "Does the organization implement logical access security software, infrastructure, and architectures to protect information assets from security events?",
    },
    {
        "key": "cc6_system_boundaries",
        "category": "common_criteria",
        "soc2_reference": "CC6.3",
        "text": "Does the organization remove access to protected information assets when appropriate, such as upon employee termination or role change?",
    },

    # CC7: System Operations
    {
        "key": "cc7_vulnerability_management",
        "category": "common_criteria",
        "soc2_reference": "CC7.1",
        "text": "Does the organization detect and monitor for vulnerabilities and threats, and evaluate the severity of identified vulnerabilities and threats?",
    },
    {
        "key": "cc7_incident_response",
        "category": "common_criteria",
        "soc2_reference": "CC7.3",
        "text": "Does the organization evaluate security events to determine whether they could or have resulted in a failure to meet objectives, and if so, take actions to achieve those objectives?",
    },

    # --- Availability (A) — 5 questions ---
    {
        "key": "a1_availability_monitoring",
        "category": "availability",
        "soc2_reference": "A1.1",
        "text": "Does the organization maintain, monitor, and evaluate current processing capacity and use of system components to manage capacity demand?",
    },
    {
        "key": "a1_backup_recovery",
        "category": "availability",
        "soc2_reference": "A1.2",
        "text": "Does the organization have environmental protections, software, data backup processes, and recovery infrastructure to meet availability commitments?",
    },
    {
        "key": "a1_disaster_recovery",
        "category": "availability",
        "soc2_reference": "A1.3",
        "text": "Has the organization developed, implemented, and tested a disaster recovery plan with documented recovery time and recovery point objectives?",
    },
    {
        "key": "a1_capacity_planning",
        "category": "availability",
        "soc2_reference": "A1.1",
        "text": "Does the organization plan and monitor capacity to ensure system availability meets service level commitments?",
    },
    {
        "key": "a1_incident_handling",
        "category": "availability",
        "soc2_reference": "A1.2",
        "text": "Are procedures in place to identify and respond to availability incidents, including escalation procedures and customer notification?",
    },

    # --- Processing Integrity (PI) — 3 questions ---
    {
        "key": "pi1_processing_accuracy",
        "category": "processing_integrity",
        "soc2_reference": "PI1.1",
        "text": "Does the organization obtain or generate relevant information to support the use of complete and accurate data in processing transactions?",
    },
    {
        "key": "pi1_error_handling",
        "category": "processing_integrity",
        "soc2_reference": "PI1.2",
        "text": "Are controls in place to detect and correct processing errors, incomplete processing, and duplicate transactions in a timely manner?",
    },
    {
        "key": "pi1_output_validation",
        "category": "processing_integrity",
        "soc2_reference": "PI1.3",
        "text": "Does the organization implement controls to validate that system outputs are complete, accurate, and distributed to authorized parties only?",
    },

    # --- Confidentiality (C) — 4 questions ---
    {
        "key": "c1_data_classification",
        "category": "confidentiality",
        "soc2_reference": "C1.1",
        "text": "Does the organization identify and classify confidential information to meet the entity's objectives related to confidentiality?",
    },
    {
        "key": "c1_confidentiality_policies",
        "category": "confidentiality",
        "soc2_reference": "C1.1",
        "text": "Are confidentiality commitments communicated to relevant parties, and are controls implemented to meet those commitments?",
    },
    {
        "key": "c1_disposal",
        "category": "confidentiality",
        "soc2_reference": "C1.2",
        "text": "Does the organization dispose of confidential information to meet the entity's objectives related to confidentiality, using secure data destruction methods?",
    },
    {
        "key": "c1_encryption",
        "category": "confidentiality",
        "soc2_reference": "C1.1",
        "text": "Is confidential information encrypted at rest and in transit to protect against unauthorized disclosure?",
    },

    # --- Privacy (P) — 3 questions ---
    {
        "key": "p1_notice",
        "category": "privacy",
        "soc2_reference": "P1.1",
        "text": "Does the organization provide notice to data subjects about its privacy practices, including the types of personal information collected and the purposes for which it is used?",
    },
    {
        "key": "p3_consent",
        "category": "privacy",
        "soc2_reference": "P3.1",
        "text": "Does the organization communicate with data subjects about their consent choices, including opt-out rights, and honor those choices?",
    },
    {
        "key": "p8_access_rights",
        "category": "privacy",
        "soc2_reference": "P8.1",
        "text": "Does the organization provide data subjects with access to their personal information for review and correction, and address any requests within a reasonable timeframe?",
    },
]


# =============================================================================
# SOC 2 POLICY TEMPLATES — 8 core policies
# =============================================================================

SOC2_POLICY_TEMPLATES = {
    "information_security": {
        "title": "Information Security Policy",
        "soc2_references": ["CC6.1", "CC6.2"],
        "content": "This policy establishes the organization's commitment to protecting information assets through administrative, technical, and physical safeguards. It defines roles, responsibilities, and minimum security requirements for all systems and data handled by the organization. The policy is reviewed annually and updated in response to significant changes in the threat landscape or business operations.",
    },
    "access_control": {
        "title": "Access Control Policy",
        "soc2_references": ["CC6.1", "CC6.3"],
        "content": "This policy governs the provisioning, modification, and revocation of logical access to systems and data, requiring unique user identifiers and role-based access aligned with least-privilege principles. Access requests must be approved by system owners, and all access must be revoked or adjusted promptly upon role change or termination. Privileged access requires additional approval and is subject to enhanced monitoring.",
    },
    "change_management": {
        "title": "Change Management Policy",
        "soc2_references": ["CC8.1"],
        "content": "This policy requires that all changes to production systems follow a documented approval, testing, and deployment process to prevent unauthorized modifications and minimize service disruption. Changes are categorized by risk level, with emergency changes subject to expedited review and post-deployment audit. All changes are logged with the approver, tester, and deployment date.",
    },
    "incident_response": {
        "title": "Incident Response Policy",
        "soc2_references": ["CC7.3", "CC7.4"],
        "content": "This policy defines procedures for detecting, containing, eradicating, and recovering from security incidents that could affect the confidentiality, availability, or integrity of the system. Incidents are triaged by severity, and affected customers are notified within timeframes specified in service agreements. Post-incident reviews are conducted to identify root causes and prevent recurrence.",
    },
    "risk_assessment": {
        "title": "Risk Assessment Policy",
        "soc2_references": ["CC3.1", "CC3.2"],
        "content": "This policy requires the organization to conduct a formal risk assessment at least annually and whenever significant changes occur to identify threats and vulnerabilities that could impact system security, availability, or integrity. Risk ratings consider likelihood and impact, and identified risks are assigned owners responsible for implementing mitigating controls. Results are reported to leadership and tracked to resolution.",
    },
    "vendor_management": {
        "title": "Vendor Management Policy",
        "soc2_references": ["CC9.2"],
        "content": "This policy establishes requirements for assessing, contracting with, and monitoring third-party vendors that have access to company systems or data, including annual security reviews and contractual security obligations. Vendors handling sensitive data must provide evidence of their own security controls (e.g., SOC 2 reports) before onboarding. Vendor access is limited to the minimum necessary and is reviewed quarterly.",
    },
    "data_retention": {
        "title": "Data Retention and Disposal Policy",
        "soc2_references": ["C1.2"],
        "content": "This policy defines retention schedules for all categories of data and requires secure disposal methods — including cryptographic erasure or physical destruction — when data reaches the end of its retention period. Disposal is documented with a certificate of destruction, and media containing confidential data must be sanitized before reuse or disposal. Data subject deletion requests are processed within 30 days.",
    },
    "business_continuity": {
        "title": "Business Continuity Policy",
        "soc2_references": ["A1.2", "A1.3"],
        "content": "This policy requires the organization to maintain and test a business continuity plan (BCP) and disaster recovery plan (DRP) to ensure critical systems and services can be restored within defined recovery time objectives (RTO) and recovery point objectives (RPO). Plans are tested at least annually through tabletop exercises or live failover tests, and results are reviewed by leadership. Lessons learned are incorporated into updated plans.",
    },
}

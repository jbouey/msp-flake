"""
GLBA Safeguards Rule Compliance Templates.

Assessment question bank and policy document templates based on
the Gramm-Leach-Bliley Act Safeguards Rule (16 CFR Part 314).
"""

# =============================================================================
# GLBA ASSESSMENT QUESTIONS — 25 questions across 5 categories
# =============================================================================

GLBA_ASSESSMENT_QUESTIONS = [
    # --- Administrative Safeguards — 8 questions ---
    {
        "key": "admin_security_program",
        "category": "administrative",
        "glba_reference": "16 CFR 314.4(a)",
        "text": "Has the organization developed, implemented, and maintained a written information security program that contains administrative, technical, and physical safeguards appropriate to the size, complexity, and sensitivity of customer financial information?",
    },
    {
        "key": "admin_designated_coordinator",
        "category": "administrative",
        "glba_reference": "16 CFR 314.4(a)",
        "text": "Has the organization designated a qualified individual responsible for overseeing, implementing, and enforcing the information security program?",
    },
    {
        "key": "admin_risk_assessment",
        "category": "administrative",
        "glba_reference": "16 CFR 314.4(b)",
        "text": "Has the organization identified and assessed the risks to customer information in each relevant area of operation, including employee training, information systems, and detecting and responding to attacks?",
    },
    {
        "key": "admin_employee_training",
        "category": "administrative",
        "glba_reference": "16 CFR 314.4(e)",
        "text": "Does the organization train and manage staff to implement the information security program, including regular security awareness training and testing?",
    },
    {
        "key": "admin_service_provider_oversight",
        "category": "administrative",
        "glba_reference": "16 CFR 314.4(f)",
        "text": "Does the organization oversee service providers that have access to customer financial information, including requiring them to implement appropriate safeguards through contractual provisions?",
    },
    {
        "key": "admin_incident_response",
        "category": "administrative",
        "glba_reference": "16 CFR 314.4(h)",
        "text": "Has the organization established an incident response plan that addresses the goals, internal processes, roles and responsibilities, and communication and documentation practices related to a security event?",
    },
    {
        "key": "admin_program_evaluation",
        "category": "administrative",
        "glba_reference": "16 CFR 314.4(i)",
        "text": "Does the organization regularly evaluate and adjust its information security program in light of the results of testing and monitoring, material changes to operations, and other relevant circumstances?",
    },
    {
        "key": "admin_board_reporting",
        "category": "administrative",
        "glba_reference": "16 CFR 314.4(i)",
        "text": "Does the designated qualified individual report to the board of directors or equivalent governing body at least annually on the overall status of the information security program and compliance with the Safeguards Rule?",
    },

    # --- Technical Safeguards — 7 questions ---
    {
        "key": "tech_access_controls",
        "category": "technical",
        "glba_reference": "16 CFR 314.4(c)(1)",
        "text": "Does the organization implement access controls on information systems, including controls to authenticate and permit access only to authorized users and to limit access to only those functions necessary for each user's role?",
    },
    {
        "key": "tech_encryption_at_rest",
        "category": "technical",
        "glba_reference": "16 CFR 314.4(c)(3)",
        "text": "Does the organization encrypt all customer financial information held or transmitted by the organization, both at rest and in transit, using current cryptographic standards?",
    },
    {
        "key": "tech_encryption_transit",
        "category": "technical",
        "glba_reference": "16 CFR 314.4(c)(3)",
        "text": "Is customer financial information encrypted in transit using TLS 1.2 or higher, and are unencrypted transmissions of such data prohibited?",
    },
    {
        "key": "tech_mfa",
        "category": "technical",
        "glba_reference": "16 CFR 314.4(c)(5)",
        "text": "Does the organization implement multi-factor authentication for any individual accessing customer financial information, including remote access and privileged account access?",
    },
    {
        "key": "tech_monitoring_logging",
        "category": "technical",
        "glba_reference": "16 CFR 314.4(c)(6)",
        "text": "Does the organization implement audit and logging controls sufficient to detect actual and attempted attacks on or intrusions into information systems containing customer financial information?",
    },
    {
        "key": "tech_vulnerability_management",
        "category": "technical",
        "glba_reference": "16 CFR 314.4(c)(7)",
        "text": "Does the organization implement procedures for the secure development of applications and systems, including vulnerability scanning and penetration testing at least annually?",
    },
    {
        "key": "tech_network_segmentation",
        "category": "technical",
        "glba_reference": "16 CFR 314.4(c)(2)",
        "text": "Does the organization implement network segmentation to isolate systems containing customer financial information from other systems and the public internet where feasible?",
    },

    # --- Physical Safeguards — 4 questions ---
    {
        "key": "phys_facility_access",
        "category": "physical",
        "glba_reference": "16 CFR 314.4(c)(1)",
        "text": "Does the organization control physical access to its facilities and systems containing customer financial information, limiting access to authorized personnel only?",
    },
    {
        "key": "phys_workstation_security",
        "category": "physical",
        "glba_reference": "16 CFR 314.4(c)(1)",
        "text": "Are workstations and portable devices that access customer financial information physically secured, and are employees required to lock screens when leaving their workstations?",
    },
    {
        "key": "phys_media_disposal",
        "category": "physical",
        "glba_reference": "16 CFR 682",
        "text": "Does the organization properly dispose of customer financial information stored on physical media by shredding, incinerating, or otherwise rendering the information unreadable?",
    },
    {
        "key": "phys_visitor_management",
        "category": "physical",
        "glba_reference": "16 CFR 314.4(c)(1)",
        "text": "Are visitor access procedures in place for areas containing systems with customer financial information, including sign-in logs and escort requirements?",
    },

    # --- Privacy Rule — 3 questions ---
    {
        "key": "privacy_notices",
        "category": "privacy",
        "glba_reference": "16 CFR 313",
        "text": "Does the organization provide customers with clear and conspicuous privacy notices at the time of establishing a customer relationship and annually thereafter, describing the organization's information sharing practices?",
    },
    {
        "key": "privacy_opt_out",
        "category": "privacy",
        "glba_reference": "16 CFR 313.7",
        "text": "Does the organization provide customers with the ability to opt out of having their nonpublic personal information shared with nonaffiliated third parties, and does the organization honor those opt-out requests?",
    },
    {
        "key": "privacy_sharing_limitations",
        "category": "privacy",
        "glba_reference": "16 CFR 313.14",
        "text": "Does the organization limit the disclosure of nonpublic personal information to nonaffiliated third parties, ensuring such disclosures are permitted under applicable GLBA exceptions or customer consent?",
    },

    # --- Disposal Rule — 3 questions ---
    {
        "key": "disposal_procedures",
        "category": "disposal",
        "glba_reference": "16 CFR 682.3",
        "text": "Does the organization have documented procedures for disposing of consumer financial information in a manner that protects against unauthorized access or use, including for electronic and paper records?",
    },
    {
        "key": "disposal_media_sanitization",
        "category": "disposal",
        "glba_reference": "16 CFR 682.3",
        "text": "Does the organization sanitize electronic media containing consumer financial information before disposal or repurposing, using methods such as overwriting, degaussing, or physical destruction?",
    },
    {
        "key": "disposal_third_party_oversight",
        "category": "disposal",
        "glba_reference": "16 CFR 682.3",
        "text": "When engaging third parties for disposal of consumer financial information, does the organization exercise due diligence in selecting and overseeing the service provider and obtaining written assurances of secure disposal?",
    },
]


# =============================================================================
# GLBA POLICY TEMPLATES — 6 core policies
# =============================================================================

GLBA_POLICY_TEMPLATES = {
    "information_security_program": {
        "title": "Written Information Security Program",
        "glba_references": ["16 CFR 314.4(a)"],
        "content": "This document constitutes the organization's written information security program (WISP) as required by the GLBA Safeguards Rule, describing the administrative, technical, and physical safeguards used to protect customer financial information. The program is proportionate to the size, complexity, and sensitivity of the customer information maintained. It is reviewed and updated at least annually and in response to material changes to operations or the threat environment.",
    },
    "risk_assessment": {
        "title": "Risk Assessment Policy",
        "glba_references": ["16 CFR 314.4(b)"],
        "content": "This policy requires the organization to identify and assess internal and external risks to the security, confidentiality, and integrity of customer financial information at least annually, covering employee practices, information systems, and incident detection and response capabilities. Risk assessment results are documented, rated by likelihood and impact, and used to prioritize safeguard improvements. All identified risks are assigned owners and tracked to resolution.",
    },
    "access_management": {
        "title": "Access Management Policy",
        "glba_references": ["16 CFR 314.4(c)(1)"],
        "content": "This policy requires that access to customer financial information be limited to authorized employees based on job function, with unique credentials and multi-factor authentication required for all access. Access is provisioned through a formal request and approval process and is revoked or adjusted promptly upon termination or role change. Privileged access is subject to enhanced logging and quarterly review.",
    },
    "incident_response": {
        "title": "Incident Response Plan",
        "glba_references": ["16 CFR 314.4(h)"],
        "content": "This plan defines the organization's procedures for preparing for, detecting, containing, eradicating, and recovering from security events affecting customer financial information, including notification obligations to customers and regulators. The plan designates an incident response team with defined roles and escalation paths, and requires post-incident reviews to improve controls. The plan is tested through tabletop exercises at least annually.",
    },
    "vendor_management": {
        "title": "Service Provider Oversight Policy",
        "glba_references": ["16 CFR 314.4(f)"],
        "content": "This policy requires due diligence before engaging service providers that access customer financial information, including security assessments and contractual provisions requiring appropriate safeguards and breach notification. Vendor security posture is reviewed at least annually, and contracts are updated when the Safeguards Rule requirements change. The organization maintains an inventory of all service providers with access to customer financial information.",
    },
    "data_disposal": {
        "title": "Data Disposal Policy",
        "glba_references": ["16 CFR 682"],
        "content": "This policy requires secure disposal of consumer financial information in both paper and electronic formats, using methods appropriate to the medium — including shredding for paper, and overwriting, degaussing, or physical destruction for electronic media. All disposals are documented with a log entry or certificate of destruction. Third-party disposal vendors must be vetted and must provide written assurances of secure destruction.",
    },
}

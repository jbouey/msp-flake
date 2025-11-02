# EMERG-002: Data Breach Response

**Version:** 1.0
**Last Updated:** 2025-10-31
**Owner:** Security Officer
**Review Cycle:** Quarterly
**Classification:** CONFIDENTIAL - Emergency Procedure

---

## What This Is

This is the emergency response procedure for security incidents that may involve unauthorized access to systems, data breaches, or compromise of cryptographic keys.

**Use this when:**
- Unauthorized access detected (someone got in who shouldn't have)
- Malware/ransomware detected on managed systems
- Cryptographic signing keys potentially compromised
- Evidence tampering detected (bundles modified, signatures invalid)
- Suspicious activity that might be a breach
- Client reports potential PHI exposure

**CRITICAL:** This is a HIPAA-regulated procedure. Breach notification to HHS is **required by law** within 60 days if PHI is involved.

---

## Time Is Critical

**HIPAA Breach Notification Deadlines:**

| Action | Deadline |
|--------|----------|
| Discover breach → Begin investigation | Immediate |
| Determine if PHI involved | 24 hours |
| Notify affected individuals | 60 days |
| Notify HHS (if >500 people) | 60 days |
| Notify HHS (if <500 people) | Annual report |
| Notify media (if >500 people in state) | 60 days |

**Don't delay. Start the clock NOW.**

---

## Immediate Response (First 15 Minutes)

### Step 1: Declare Security Incident

**Who can declare:** Security Officer, Operations Manager, or On-Call Engineer

```bash
# Send emergency notification
cat <<EOF | mail -s "[SECURITY INCIDENT] Potential Data Breach" \
  security@msp.com,ops@msp.com,ceo@msp.com

SECURITY INCIDENT DECLARED

Time: $(date -u)
Declared by: $(whoami)
Incident Type: [Unauthorized Access / Malware / Key Compromise / Evidence Tampering / Unknown]

Brief Description:
[What you know right now - be specific]

Initial Actions Taken:
[What you've done so far]

Next Steps:
- Assembling incident response team
- Preserving evidence
- Beginning containment

Status updates every 30 minutes.

DO NOT REPLY ALL - Use secure channel
EOF
```

---

### Step 2: Assemble Incident Response Team

**Required team members:**

- Security Officer (Incident Commander)
- Operations Manager (Technical Lead)
- Operations Engineer (Forensics/Investigation)
- Legal Counsel (Breach notification requirements)
- CEO/Founder (Business decisions, client communication)
- Client Success Manager (Client coordination)

**Communication channel:** Use secure Signal group or encrypted email (NOT regular Slack/email if breach involves credentials)

---

### Step 3: Preserve Evidence

**Do NOT:**
- Shut down affected systems (you'll lose memory evidence)
- Delete logs
- "Clean up" or "fix" things yet
- Tell client details (until you know what happened)

**DO:**

```bash
# 1. Snapshot affected systems immediately
INSTANCE_ID="i-1234567890abcdef"
INCIDENT_ID="SEC-$(date +%Y%m%d-%H%M%S)"

aws ec2 create-snapshot \
  --volume-id $(aws ec2 describe-instances --instance-ids $INSTANCE_ID \
    --query 'Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId' \
    --output text) \
  --description "Security Incident $INCIDENT_ID - DO NOT DELETE" \
  --tag-specifications "ResourceType=snapshot,Tags=[{Key=SecurityIncident,Value=$INCIDENT_ID},{Key=PreserveUntil,Value=$(date -d '+2 years' +%Y-%m-%d)}]"

# 2. Copy logs before they rotate
AFFECTED_HOSTS="mcp-server.msp.internal srv-01.clinic-001.internal"

for host in $AFFECTED_HOSTS; do
  echo "Copying logs from: $host"

  ssh $host "sudo tar czf /tmp/forensic-logs-${INCIDENT_ID}.tar.gz \
    /var/log/ \
    /var/lib/msp/evidence/ \
    /etc/ \
    /root/.bash_history \
    /home/*/.bash_history"

  scp $host:/tmp/forensic-logs-${INCIDENT_ID}.tar.gz \
    /secure/forensics/${INCIDENT_ID}/${host}-logs.tar.gz

  # Hash for integrity
  sha256sum /secure/forensics/${INCIDENT_ID}/${host}-logs.tar.gz > \
    /secure/forensics/${INCIDENT_ID}/${host}-logs.tar.gz.sha256
done

# 3. Capture memory dumps (if possible without disrupting)
# Only if you have tools installed - don't install new tools during incident
ssh $host "sudo gcore -o /tmp/memory-dump $(pgrep -f mcp-server)"

# 4. Document current state
ssh $host "sudo lsof -nP > /tmp/open-files.txt"
ssh $host "sudo netstat -antup > /tmp/network-connections.txt"
ssh $host "sudo ps auxf > /tmp/process-tree.txt"
```

**Critical:** Store all forensic data on separate, isolated storage. Don't use the same S3 bucket as normal operations.

---

## Investigation Phase (First Hour)

### Step 4: Initial Triage

**Answer these questions:**

1. **What happened?**
   - Unauthorized login detected?
   - Malware found?
   - Evidence tampering?
   - System behavior anomaly?

2. **When did it happen?**
   - First sign of compromise?
   - How long has attacker had access?

3. **What systems are affected?**
   - Which servers?
   - Which clients?
   - Core infrastructure or client nodes?

4. **Is PHI involved?**
   - **THIS IS THE CRITICAL QUESTION FOR HIPAA**
   - Remember: We process system metadata ONLY
   - PHI involvement requires breach notification

---

### Step 5: Determine PHI Involvement

**Key question: Did the attacker access Protected Health Information?**

**Our system boundaries:**

| System | Potential PHI Exposure | Risk Level |
|--------|----------------------|------------|
| **MCP Server** | None - processes incident metadata only | Low |
| **Evidence Bundles** | None - contains system logs, no PHI | Low |
| **WORM Storage** | None - evidence bundles don't contain PHI | Low |
| **Client Logs** | **MAYBE** - logs might accidentally contain PHI | **MEDIUM** |
| **Client EHR** | **YES** - direct PHI access | **HIGH** |

**Investigation checklist:**

```bash
# Check if attacker accessed client systems
grep -r "Accepted publickey\|Accepted password" /secure/forensics/${INCIDENT_ID}/*/auth.log

# Look for suspicious commands
grep -r "cat\|less\|grep\|vim\|nano" /secure/forensics/${INCIDENT_ID}/*/.bash_history

# Check database access logs (if client has DB we monitor)
grep -r "SELECT.*FROM.*patients\|SELECT.*FROM.*records" /secure/forensics/${INCIDENT_ID}/*/postgresql.log

# Check file access
grep -r "/data/\|/ehr/\|/records/" /secure/forensics/${INCIDENT_ID}/*/audit.log

# Check evidence bundles for PHI leakage
find /secure/forensics/${INCIDENT_ID}/ -name "*.json" -exec \
  grep -l "SSN\|DOB.*[0-9]\{2\}/[0-9]\{2\}/[0-9]\{4\}\|MRN" {} \;
```

**Decision tree:**

```
Did attacker access client EHR/database directly?
├─ YES → PHI breach (HIPAA notification required)
└─ NO → Did attacker access client logs?
    ├─ YES → Check if logs contain PHI
    │   ├─ YES → PHI breach
    │   └─ NO → System breach only (no HIPAA notification)
    └─ NO → System breach only
```

---

### Step 6: Containment

**If attacker still has access, cut them off immediately:**

```bash
# 1. Rotate all credentials
# MCP server
/opt/msp/scripts/emergency-rotate-keys.sh --all

# Client SSH access
for client in $(cat /etc/msp/clients.txt); do
  ssh root@mgmt.${client}.msp.internal "
    # Disable password auth
    sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
    systemctl restart sshd

    # Expire all SSH certificates
    find /etc/ssh/ca/ -name '*.pub' -exec mv {} {}.revoked \;

    # Force all users to re-authenticate
    pkill -u <suspicious-user>
  "
done

# 2. Block suspicious IPs
ATTACKER_IP="1.2.3.4"

aws ec2 describe-security-groups \
  --query 'SecurityGroups[*].GroupId' \
  --output text | \
  xargs -I {} aws ec2 revoke-security-group-ingress \
    --group-id {} \
    --ip-permissions IpProtocol=tcp,FromPort=0,ToPort=65535,IpRanges=[{CidrIp=${ATTACKER_IP}/32}]

# 3. Isolate compromised systems
# If you know which system is compromised, isolate it
aws ec2 modify-instance-attribute \
  --instance-id $COMPROMISED_INSTANCE \
  --groups sg-forensics-isolation  # Security group with no inbound/outbound

# 4. Revoke API keys/tokens
aws iam list-access-keys --user-name msp-automation --output text | \
  awk '{print $2}' | \
  xargs -I {} aws iam delete-access-key --access-key-id {} --user-name msp-automation

# Generate new keys
aws iam create-access-key --user-name msp-automation
```

---

## Evidence Analysis (First 4 Hours)

### Step 7: Forensic Investigation

**What to look for:**

```bash
# 1. Unauthorized access attempts
grep "Failed password\|authentication failure" /secure/forensics/${INCIDENT_ID}/*/auth.log | \
  awk '{print $1, $2, $3, $11}' | \
  sort | uniq -c | sort -rn | head -20

# Shows: Most common failed login IPs

# 2. Successful unauthorized logins
grep "Accepted publickey\|Accepted password" /secure/forensics/${INCIDENT_ID}/*/auth.log | \
  grep -v "known-good-user-1\|known-good-user-2"

# 3. Privilege escalation
grep "sudo:\|COMMAND=" /secure/forensics/${INCIDENT_ID}/*/secure | \
  grep -v "known-good-user"

# 4. Unusual processes
# Compare running processes to baseline
diff <(sort baseline-processes.txt) \
     <(sort /secure/forensics/${INCIDENT_ID}/*/process-tree.txt) | \
     grep "^>"

# 5. Network connections to suspicious IPs
grep -v "ESTABLISHED.*:443\|ESTABLISHED.*:80" \
  /secure/forensics/${INCIDENT_ID}/*/network-connections.txt

# 6. File modifications
find /secure/forensics/${INCIDENT_ID}/ -name "*.tar.gz" -exec tar -tzf {} \; | \
  grep "\.bash_history$\|\.ssh/authorized_keys$\|\.ssh/known_hosts$" | \
  while read file; do
    echo "Checking: $file"
    # Extract and review
  done
```

---

### Step 8: Determine Attack Vector

**How did they get in?**

Common vectors:

1. **Stolen credentials**
   - Check for leaked keys on GitHub, pastebin
   - Review access logs for unusual login locations/times
   - Check if MFA was bypassed

2. **Vulnerability exploitation**
   - Check for known CVEs in running software
   - Review recent security patches (were we behind?)
   - Check for zero-day indicators

3. **Social engineering**
   - Review recent support tickets (attacker impersonating client?)
   - Check for phishing emails to staff
   - Review MFA reset requests

4. **Insider threat**
   - Recent employee departures?
   - Disgruntled staff?
   - Access beyond job requirements?

5. **Supply chain**
   - Compromised dependencies (check npm/pip packages)
   - Malicious Docker images
   - Compromised GitHub Actions

**Document findings:**

```bash
cat > /secure/forensics/${INCIDENT_ID}/investigation-summary.md <<EOF
# Security Incident Investigation Summary

**Incident ID:** ${INCIDENT_ID}
**Date:** $(date -u)
**Investigator:** $(whoami)

## Attack Vector

[How they got in]

## Timeline

- [Timestamp]: First sign of compromise
- [Timestamp]: Attacker gained access
- [Timestamp]: Suspicious activity detected
- [Timestamp]: Incident declared
- [Timestamp]: Attacker access revoked

## Systems Affected

- [List of systems]

## Data Accessed

- [List of data/files accessed]

## PHI Involved

[YES/NO - if yes, describe what PHI]

## Attacker Actions

1. [What they did first]
2. [What they did next]
3. [...]

## Evidence Preserved

- EC2 snapshots: [snapshot IDs]
- Log archives: /secure/forensics/${INCIDENT_ID}/
- Memory dumps: [if captured]

## Next Steps

1. [Remediation actions needed]
2. [...]
EOF
```

---

## Notification Phase (If PHI Involved)

### Step 9: Breach Determination

**Formal breach determination requires:**

- **Legal Counsel review** (this is a legal decision, not just technical)
- **Risk assessment** (low probability PHI compromised ≠ automatic notification)
- **Documentation** of decision process

**HIPAA "Breach" definition:**

> Acquisition, access, use, or disclosure of PHI in a manner not permitted under the Privacy Rule which **compromises the security or privacy** of the PHI.

**Exceptions (NOT a breach):**

1. **Unintentional acquisition/access** by workforce member acting in good faith
2. **Inadvertent disclosure** from authorized person to another authorized person at same entity
3. **Good faith belief** that unauthorized person couldn't retain the information

**Risk assessment required:**

Must consider:
1. Nature and extent of PHI involved
2. Unauthorized person who used/received PHI
3. Whether PHI was actually acquired or viewed
4. Extent to which risk has been mitigated

**Document the decision:**

```bash
cat > /secure/forensics/${INCIDENT_ID}/breach-determination.md <<EOF
# HIPAA Breach Determination

**Date:** $(date -u)
**Incident ID:** ${INCIDENT_ID}

## Risk Assessment

### 1. Nature and Extent of PHI

[Describe what PHI was involved, how many records]

### 2. Unauthorized Person

[Who accessed it? External attacker? Employee?]

### 3. Actual Acquisition or Viewing

[Was PHI actually viewed, or just potentially accessible?]

### 4. Mitigation

[What steps reduce the risk?]

## Determination

After review by Security Officer and Legal Counsel:

☐ This IS a breach requiring notification under 45 CFR 164.404
☐ This is NOT a breach (low probability of compromise)

**Rationale:**
[Explain decision]

**Signed:**
Security Officer: ___________________ Date: ___________
Legal Counsel: _____________________ Date: ___________

EOF
```

---

### Step 10: Breach Notification (If Required)

**If Legal determines this is a reportable breach:**

#### A. Notify Affected Individuals (Within 60 Days)

**Notification must include:**
- Description of what happened
- Types of PHI involved
- Steps individuals should take
- What we're doing to investigate and prevent recurrence
- Contact information

**Template:**

```
[Date]

Dear [Individual],

We are writing to inform you of a data security incident that may have
involved some of your protected health information.

WHAT HAPPENED

On [date], we discovered that an unauthorized person gained access to
our healthcare IT compliance monitoring system. Our investigation
determined that [describe what attacker accessed].

WHAT INFORMATION WAS INVOLVED

The incident may have involved the following types of your information:
[List: names, addresses, medical record numbers, etc.]

IMPORTANT: This incident involved our IT monitoring system, not your
healthcare provider's electronic health records. Your medical records
were not directly accessed.

WHAT WE ARE DOING

We have taken the following steps:
1. [Immediate containment actions]
2. [Investigation completed]
3. [Preventive measures implemented]
4. [Notification to law enforcement - if applicable]

WHAT YOU CAN DO

We recommend you take the following precautions:
1. Monitor your credit reports for suspicious activity
2. Be alert for phishing emails or suspicious contact
3. [Other relevant steps]

We are offering [12 months] of free credit monitoring services.
To enroll, call [phone] or visit [website] by [date].

FOR MORE INFORMATION

If you have questions, please contact our dedicated assistance line:
Phone: [number]
Email: [email]
Hours: [hours]

We take the security of your information very seriously and sincerely
apologize for this incident.

Sincerely,

[CEO Name]
[Company Name]
```

**Delivery method:**
- First class mail (required)
- Email (if individual agreed to electronic communication)
- Substitute notice (if >10 individuals have insufficient/out-of-date contact info)

---

#### B. Notify HHS Office for Civil Rights

**If breach affects ≥500 individuals:**

- Notify HHS **within 60 days** of discovery
- Submit via HHS Breach Portal: https://ocrportal.hhs.gov/ocr/breach/wizard_breach.jsf

**If breach affects <500 individuals:**

- Maintain log of breaches
- Submit annual report to HHS (within 60 days of end of calendar year)

**Information HHS requires:**

```
1. Name and contact info of covered entity
2. Business associate involved (if applicable) - THAT'S US
3. Date of breach discovery
4. Date of breach occurrence (if different)
5. Number of individuals affected
6. Description of incident
7. Type of PHI involved
8. Steps taken to mitigate harm
9. Contact person for HHS to reach
```

---

#### C. Notify Media (If Breach Affects >500 Individuals in Same State)

**Requirement:** Notify prominent media outlets serving the state

**Timeline:** Same time as individual notification (within 60 days)

**Method:** Press release to major newspapers, TV stations

---

### Step 11: Client Notification

**Even if not a HIPAA breach, notify affected clients:**

```
Subject: [URGENT] Security Incident Notification - [Your Company]

Dear [Client Name],

We are writing to inform you of a security incident affecting our
MSP HIPAA compliance monitoring system.

INCIDENT SUMMARY

On [date], we discovered [brief description]. We immediately began
an investigation and took steps to contain the incident.

IMPACT ON YOUR ORGANIZATION

Based on our investigation:
- [What systems were affected]
- [What data was accessed]
- [Whether PHI was involved - be specific]

We have determined that [this is/is not] a reportable breach under
HIPAA affecting your organization.

ACTIONS WE HAVE TAKEN

1. [Containment actions]
2. [Investigation completed]
3. [Security improvements implemented]
4. [Notification to law enforcement - if applicable]

ACTIONS YOU SHOULD TAKE

[Client-specific recommendations]

We have prepared a detailed incident report with forensic evidence
available for your review. Please contact us to schedule a briefing.

ONGOING SUPPORT

We are committed to supporting you through this incident:
- Dedicated incident response line: [phone]
- Daily status updates until resolved
- Assistance with your breach notification (if required)
- [Free services/compensation if applicable]

We sincerely apologize for this incident and any concern it may cause.
We are taking this matter extremely seriously.

Please contact us at your earliest convenience.

Sincerely,

[CEO Name]
[Company Name]
[Contact Info]
```

---

## Remediation Phase

### Step 12: Fix Root Cause

**Based on attack vector, implement fixes:**

| Attack Vector | Remediation |
|--------------|-------------|
| **Stolen credentials** | Rotate all credentials, enforce MFA, implement key rotation policy |
| **Vulnerability** | Patch immediately, scan for similar vulns, improve patch SLA |
| **Social engineering** | Staff training, improve verification procedures, phone verification |
| **Insider threat** | Improve access controls, implement least privilege, audit logging |
| **Supply chain** | Pin dependency versions, implement SBOM scanning, verify signatures |

**Document changes:**

```bash
# Update baseline with security improvements
cd msp-platform/baseline/

git checkout -b security-hardening-${INCIDENT_ID}

# Example: Add MFA requirement
cat >> hipaa-v1.yaml <<EOF

# Added ${INCIDENT_ID} - Enforce MFA
security:
  mfa:
    required: true
    grace_period_days: 0  # Reduced from 30
    enforcement_level: strict

  ssh:
    cert_lifetime_hours: 4  # Reduced from 8
    pubkey_auth_only: true
    password_auth: false

  audit:
    failed_login_threshold: 3  # Reduced from 5
    lockout_duration_minutes: 30  # Increased from 15
EOF

git add hipaa-v1.yaml
git commit -m "Security hardening: Response to ${INCIDENT_ID}

- Enforce MFA with zero grace period
- Reduce SSH cert lifetime to 4 hours
- Stricter failed login thresholds

Root cause: [brief description]
Prevention: [what this change prevents]

Incident: ${INCIDENT_ID}"

git push origin security-hardening-${INCIDENT_ID}
```

---

### Step 13: Restore Operations

**Gradual, verified restoration:**

```bash
# 1. Deploy hardened baseline to test environment
cd terraform/test/
terraform apply -var="baseline_version=security-hardening-${INCIDENT_ID}"

# 2. Run security validation
/opt/msp/scripts/security-validation.sh --strict

# Expected: All checks pass

# 3. Deploy to staging
cd terraform/staging/
terraform apply -var="baseline_version=security-hardening-${INCIDENT_ID}"

# Monitor for 24 hours

# 4. Staged rollout to production
# 10% of clients first
/opt/msp/scripts/staged-rollout.sh \
  --baseline security-hardening-${INCIDENT_ID} \
  --percentage 10 \
  --monitor-hours 48

# If no issues, continue to 50%, then 100%
```

---

## Post-Incident Phase

### Step 14: Post-Incident Review (Required)

**Conduct within 7 days of incident resolution:**

```markdown
# Security Incident Post-Incident Review

**Date:** [Date of PIR meeting]
**Incident ID:** ${INCIDENT_ID}
**Facilitator:** Security Officer
**Attendees:** [List all team members involved]

## Executive Summary

[1-paragraph summary for board/investors]

## Incident Timeline

| Time | Event |
|------|-------|
| [Timestamp] | [Event] |
| ... | ... |

## What Went Well

1. [Positive aspect]
2. [Positive aspect]

## What Went Wrong

1. [Issue]
2. [Issue]

## Root Cause

[Detailed root cause analysis]

## Contributing Factors

1. [Factor]
2. [Factor]

## Action Items

| Action | Owner | Due Date | Status |
|--------|-------|----------|--------|
| [Action] | [Name] | [Date] | Open |

## Recommendations

1. [Strategic recommendation]
2. [Process improvement]
3. [Technical improvement]

## Lessons Learned

[Key takeaways for future incidents]

## Financial Impact

- Incident response costs: $[amount]
- Client compensation: $[amount]
- Lost revenue: $[amount]
- Legal fees: $[amount]
- **Total:** $[amount]

## Signature

Security Officer: ___________________ Date: ___________
CEO: ______________________________ Date: ___________
```

---

### Step 15: Update Incident Response Plan

**After every incident, update this SOP:**

```bash
cd msp-platform/docs/sop/

git checkout -b update-breach-response-${INCIDENT_ID}

# Add new section based on lessons learned
cat >> EMERG-002_DATA_BREACH_RESPONSE.md <<EOF

## Appendix: ${INCIDENT_ID} Lessons Learned

**Incident Type:** [Type]
**Date:** [Date]

**Key Lessons:**

1. [Lesson 1]
   - **What happened:** [Description]
   - **Improvement:** [What we changed]

2. [Lesson 2]
   ...

**New Procedures:**

[Any new steps added to this SOP]

EOF

git commit -m "Update breach response plan based on ${INCIDENT_ID}"
git push
```

---

## Testing & Drills

**Required:** Annual breach response tabletop exercise

**Scenario examples:**

1. **Stolen laptop with SSH keys**
   - Timeline: Discover laptop missing
   - Decision: Is this a breach?
   - Actions: Revoke keys, assess access

2. **Ransomware on client system**
   - Timeline: Malware detected
   - Decision: Restore from backup or pay ransom?
   - Actions: Isolate, restore, investigate

3. **Employee clicked phishing link**
   - Timeline: Employee reports suspicious email
   - Decision: Assume compromise or investigate?
   - Actions: Password reset, scan systems

**Document drill results:**

```bash
cat > /var/msp/drills/breach-response-drill-$(date +%Y-%m-%d).md <<EOF
# Breach Response Drill - $(date +%Y-%m-%d)

**Scenario:** [Description]
**Participants:** [Names]
**Duration:** [Time]

## Performance

**Response Time:**
- Initial detection: [minutes]
- Incident declared: [minutes]
- Team assembled: [minutes]
- Containment completed: [minutes]

**Decision Quality:**
- Breach determination: [Correct/Incorrect]
- Notification timeline: [Within compliance/Delayed]
- Containment effectiveness: [Effective/Needs improvement]

## Issues Identified

1. [Issue]
2. [Issue]

## Improvements Needed

1. [Action item]
2. [Action item]

**Next Drill:** [Date + 12 months]
EOF
```

---

## Emergency Contacts

**Security Incident Response Team:**

| Role | Name | Phone | Email |
|------|------|-------|-------|
| **Security Officer** (Incident Commander) | [Name] | [Phone] | security@msp.com |
| **Legal Counsel** | [Firm] | [Phone] | legal@law firm.com |
| **CEO** | [Name] | [Phone] | ceo@msp.com |
| **Operations Manager** | [Name] | [Phone] | ops@msp.com |
| **Forensics Consultant** | [Firm] | [Phone] | [Email] |

**External Resources:**

| Resource | Contact | Purpose |
|----------|---------|---------|
| **FBI Cyber Division** | 1-855-292-3937 | Report cybercrime |
| **HHS OCR Breach Portal** | https://ocrportal.hhs.gov | HIPAA breach notification |
| **Cyber Insurance** | [Carrier, Policy #] | Claims, legal support |
| **AWS Security** | 1-866-936-7133 | Cloud infrastructure incidents |

---

## Related Documents

- **SOP-002:** Incident Response (non-security incidents)
- **SOP-003:** Disaster Recovery
- **SOP-004:** Client Escalation
- **OP-002:** Evidence Pipeline Operations
- **OP-005:** Cryptographic Key Management
- **EMERG-003:** Key Compromise Response

---

## Revision History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2025-10-31 | Initial version | Security Team |

---

**Document Status:** ✅ Active
**Next Review:** 2026-01-31 (Quarterly)
**Owner:** Security Officer
**Classification:** CONFIDENTIAL

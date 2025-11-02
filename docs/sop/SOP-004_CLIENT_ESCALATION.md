# SOP-004: Client Escalation Procedures

**Version:** 1.0
**Last Updated:** 2025-10-31
**Owner:** Operations Manager
**Review Cycle:** Monthly
**Required Reading:** All Operations Engineers, Client Success Managers

---

## Purpose

This Standard Operating Procedure defines escalation procedures for client-facing issues, ensuring timely resolution and clear communication. This SOP covers:

- Client-initiated support requests (not automated incident handling)
- SLA breach notifications and escalation
- Client complaints and dissatisfaction management
- Emergency client coordination
- Proactive client communication for platform issues

**Note:** This SOP is for client communication and escalation, NOT technical incident response. For technical incidents, see SOP-002: Incident Response.

---

## Scope

### In Scope
- Client support requests via phone, email, portal
- SLA breach notifications
- Client complaints about service quality
- Emergency coordination with client IT staff
- Client education and training requests
- Contract questions and service scope discussions

### Out of Scope
- Technical infrastructure incidents (see SOP-002)
- Disaster recovery procedures (see SOP-003)
- Client onboarding (see SOP-010)
- Compliance audit support (see SOP-011)

---

## Roles and Responsibilities

| Role | Responsibilities |
|------|-----------------|
| **Client Success Manager** | Primary client relationship owner, escalation coordinator |
| **Operations Engineer** | Technical support, incident explanation |
| **Operations Manager** | SLA compliance, escalation approval, client satisfaction |
| **Security Officer** | Security incident communication, breach notifications |
| **Account Executive** | Contract discussions, service expansion, billing issues |

---

## Support Channels & SLAs

### Support Channel Priority

| Channel | Priority | Response SLA | Availability |
|---------|----------|-------------|--------------|
| **Emergency Hotline** | P0 - Critical | 15 minutes | 24/7 |
| **Email (ops@msp.com)** | P1 - High | 2 hours | Business hours |
| **Client Portal Ticket** | P2 - Medium | 4 hours | Business hours |
| **Scheduled Call** | P3 - Low | Next business day | Business hours |

**Business Hours:** Monday-Friday, 09:00-17:00 Eastern Time (14:00-22:00 UTC)

---

## Escalation Tiers

### Tier 1: Operations Engineer (First Response)

**Handles:**
- Technical questions about dashboards, evidence bundles
- Explanation of automated incident handling
- Basic compliance reporting questions
- Service status inquiries

**Escalates to Tier 2 if:**
- Client dissatisfied with response
- Issue requires manager approval
- SLA breach imminent
- Technical issue beyond engineer expertise

---

### Tier 2: Operations Manager

**Handles:**
- SLA breach discussions
- Client concerns about service quality
- Approval for emergency actions
- Resource allocation decisions
- Escalation policy exceptions

**Escalates to Tier 3 if:**
- Client threatens to cancel service
- Legal or contractual dispute
- Security/privacy concerns
- Executive involvement requested

---

### Tier 3: Executive Team

**Handles:**
- Contract disputes
- Service cancellation discussions
- Legal/regulatory concerns
- Strategic partnership discussions
- Major service failures (P0 disasters)

---

## Procedures

### Procedure 1: Client-Initiated Support Request

#### 1.1 Receive Support Request

**Email Request (ops@msp.com):**

Client emails arrive in shared inbox → Auto-creates ticket in support system

**Phone Request (555-MSP-HELP):**

On-call engineer answers → Creates ticket manually

**Portal Request:**

Client submits via https://support.msp.com → Auto-creates ticket

---

#### 1.2 Classify Request Priority

| Priority | Criteria | Examples | Response SLA |
|----------|----------|----------|-------------|
| **P0 - Emergency** | Service outage, data loss, security incident | "All our dashboards are down," "Backup failed for 3 days" | 15 minutes |
| **P1 - Urgent** | SLA affecting, compliance concern | "Audit tomorrow, need evidence bundles," "Cert expired" | 2 hours |
| **P2 - Normal** | General question, non-urgent issue | "How do I read the compliance packet?," "Dashboard slow" | 4 hours |
| **P3 - Low** | Information request, training | "Can you explain how MFA monitoring works?" | Next business day |

---

#### 1.3 Initial Response Template

**P0 - Emergency:**

```
Subject: RE: [Client Ticket #12345] - EMERGENCY RESPONSE

Dear [Client Name],

We have received your emergency support request and are treating this
as a P0 critical incident.

Ticket #: 12345
Priority: P0 - Emergency
Assigned To: [Engineer Name]
Response SLA: 15 minutes ✅

I am personally assigned to this issue and will keep you updated every
15 minutes until resolved.

Current Status:
[Brief description of what we're doing right now]

Next Update: [Timestamp - 15 minutes from now]

If you need immediate assistance, please call me directly:
[Engineer Name]: [Phone Number]

We will resolve this as quickly as possible.

Best regards,
[Engineer Name]
MSP Operations Team
```

**P1 - Urgent:**

```
Subject: RE: [Client Ticket #12345]

Dear [Client Name],

Thank you for contacting MSP support. We have received your request
and classified it as P1 - Urgent.

Ticket #: 12345
Priority: P1 - Urgent
Assigned To: [Engineer Name]
Response SLA: 2 hours ✅

Issue Summary:
[Brief restatement of the issue]

Estimated Resolution Time:
[X hours/days]

I will provide you with an update within 2 hours.

If this is a critical emergency requiring immediate attention, please
call our emergency hotline: 555-MSP-HELP

Best regards,
[Engineer Name]
MSP Operations Team
```

**P2 - Normal:**

```
Subject: RE: [Client Ticket #12345]

Dear [Client Name],

Thank you for contacting MSP support.

Ticket #: 12345
Priority: P2 - Normal
Assigned To: [Engineer Name]
Response SLA: 4 hours ✅

I will investigate your request and provide a detailed response within
4 hours.

Best regards,
[Engineer Name]
MSP Operations Team
```

---

#### 1.4 Investigation & Resolution

**Operations Engineer:**

1. **Review ticket details**
2. **Check client dashboard** for recent incidents
3. **Review evidence bundles** if compliance-related
4. **Check logs** if technical issue
5. **Consult runbook library** for standard solutions

**Common Request Types:**

| Request Type | Investigation Steps | Resolution |
|-------------|-------------------|-----------|
| **Dashboard question** | Review client dashboard, check permissions | Provide explanation or grant access |
| **Evidence bundle request** | Download from WORM storage | Email or portal delivery |
| **Compliance question** | Review baseline, controls map | Provide HIPAA citation or documentation |
| **Service status** | Check monitoring dashboard | Provide status update |
| **Training request** | Schedule call | Provide screen share demo or documentation |

---

#### 1.5 Resolution Communication

**Resolution Email Template:**

```
Subject: RE: [Client Ticket #12345] - RESOLVED

Dear [Client Name],

Your support request has been resolved.

Ticket #: 12345
Resolution Time: [X hours] (SLA: [Y hours] ✅)

Issue Summary:
[Brief description of the issue]

Resolution:
[What we did to fix it]

Root Cause:
[Why it happened - if applicable]

Prevention:
[Steps we've taken to prevent recurrence - if applicable]

Documentation:
[Links to relevant documentation or evidence]

If you have any further questions or if this issue recurs, please
don't hesitate to contact us by replying to this email or calling
our support line.

Your Satisfaction:
We value your feedback. If you have a moment, please rate this
support interaction: [Survey Link]

Best regards,
[Engineer Name]
MSP Operations Team

Ticket #12345 - Closed
```

---

### Procedure 2: SLA Breach Notification & Escalation

#### 2.1 Automated SLA Breach Detection

**Monitoring System Alerts:**

```bash
# SLA monitoring script (runs every 15 minutes)
/opt/msp/scripts/sla-monitor.sh

# Checks:
# - Incident resolution time vs. SLA target
# - Support ticket response time vs. SLA target
# - Evidence bundle generation lag
# - Dashboard availability

# If SLA breach detected:
# - Send PagerDuty alert to Operations Manager
# - Auto-escalate ticket to Tier 2
# - Notify client automatically
```

---

#### 2.2 Proactive SLA Breach Notification

**If Operations Engineer anticipates SLA breach:**

```
Subject: [SLA UPDATE] Ticket #12345 - Extended Resolution Time

Dear [Client Name],

I am writing to inform you that your support request (Ticket #12345)
will require additional time to resolve and may exceed our standard
SLA target.

Original SLA: 4 hours
Current Status: In Progress (3 hours elapsed)
Estimated Resolution: 6 hours (2 hours beyond SLA)

Reason for Delay:
[Brief explanation - e.g., "This issue requires coordination with your
IT team to update firewall rules"]

What We're Doing:
[Detailed explanation of investigation and resolution steps]

Escalation:
This ticket has been escalated to our Operations Manager, who is now
personally overseeing the resolution.

We apologize for the extended timeline and appreciate your patience.
I will provide hourly updates until this is resolved.

Next Update: [Timestamp - 1 hour from now]

Contact:
Operations Manager: [Name] - [Phone] - [Email]

Best regards,
[Engineer Name]
MSP Operations Team
```

**Operations Manager Approval Required:**

Before sending SLA breach notification, Operations Engineer must:
1. Notify Operations Manager (email + Slack/Teams)
2. Get approval for extended timeline
3. Document reason for breach in ticket

---

#### 2.3 SLA Breach Post-Mortem

**Required for all SLA breaches:**

Within 48 hours of resolution, Operations Manager conducts brief post-mortem:

```markdown
# SLA Breach Post-Mortem

**Ticket:** #12345
**Client:** clinic-001 (Anytown Family Medicine)
**Issue:** Dashboard inaccessible
**SLA Target:** 4 hours
**Actual Resolution:** 6.5 hours
**Breach Margin:** 2.5 hours

## Timeline
- 10:00 - Ticket received
- 10:15 - Engineer assigned
- 11:00 - Investigation identified root cause (AWS S3 outage)
- 13:00 - Waiting for AWS resolution
- 14:30 - SLA breach notification sent to client
- 16:30 - AWS resolved outage
- 16:45 - Dashboard restored
- 17:00 - Client notified of resolution

## Root Cause
AWS S3 outage in us-east-1 region (beyond our control)

## Client Impact
Unable to view compliance dashboard for 6.5 hours. No impact on
automated monitoring or evidence generation (continued normally).

## Prevention
- Implement multi-region dashboard deployment
- Add "degraded mode" dashboard (static HTML) for outages

## Client Communication
Client satisfied with proactive notification and hourly updates.
No compensation requested.

## Action Items
1. Deploy multi-region dashboard - Infrastructure Lead - 2025-11-15
2. Create degraded mode dashboard - Ops Engineer - 2025-11-07
```

---

### Procedure 3: Client Complaint Management

#### 3.1 Receive Complaint

**Complaint Channels:**
- Email to ops@msp.com or manager@msp.com
- Phone call expressing dissatisfaction
- Portal feedback/survey (low rating)
- Escalation from support ticket

**Complaint Classification:**

| Level | Definition | Examples |
|-------|-----------|----------|
| **Minor** | Temporary inconvenience, resolved | "Dashboard was slow this morning" |
| **Moderate** | Service quality concern, no SLA breach | "Evidence bundles are confusing" |
| **Major** | SLA breach, service failure | "Backup failed for 3 days unnoticed" |
| **Critical** | Threatens contract cancellation | "We're evaluating other vendors" |

---

#### 3.2 Immediate Response (Tier 1)

**Operations Engineer:**

```
Subject: RE: [Complaint] - Immediate Response

Dear [Client Name],

Thank you for bringing this to our attention. We take all client
feedback seriously and are committed to resolving your concerns.

I have escalated this issue to our Operations Manager, who will
personally review your feedback and contact you within 2 hours.

In the meantime, please let me know if there is anything I can do
to assist you immediately.

Best regards,
[Engineer Name]
MSP Operations Team
```

**Escalate to Operations Manager immediately** (all complaints, regardless of severity)

---

#### 3.3 Operations Manager Response (Tier 2)

**Within 2 hours:**

```
Subject: RE: [Complaint] - Operations Manager Response

Dear [Client Name],

I am [Manager Name], the Operations Manager for MSP. I have reviewed
your feedback regarding [issue] and would like to personally address
your concerns.

Summary of Your Concern:
[Restatement of the issue in client's own words]

What Happened:
[Factual explanation of the incident/issue]

What We're Doing About It:
1. [Immediate action taken]
2. [Root cause investigation]
3. [Long-term prevention]

I would like to schedule a call with you to discuss this further and
ensure we fully address your concerns. Please let me know your
availability this week.

Alternatively, if you prefer, I am available to speak now at [phone].

We value your business and are committed to providing exceptional
service. Thank you for your patience as we work through this issue.

Best regards,
[Manager Name]
Operations Manager
MSP
[Phone] | [Email]
```

---

#### 3.4 Resolution & Follow-Up

**Operations Manager Call Script:**

1. **Acknowledge and apologize**
   - "Thank you for bringing this to our attention. I apologize for [specific issue]."

2. **Listen actively**
   - Let client fully explain their concern
   - Take notes
   - Ask clarifying questions

3. **Explain what happened**
   - Provide technical details (but avoid jargon)
   - Show evidence bundles/logs if applicable
   - Be transparent about root cause

4. **Outline resolution**
   - What we've done already
   - What we're doing next
   - Timeline for completion

5. **Prevent recurrence**
   - Specific actions we're taking
   - Documentation updates
   - Process improvements

6. **Offer compensation (if appropriate)**
   - Service credit
   - Extended support
   - Free training session
   - Custom reporting

7. **Confirm satisfaction**
   - "Does this resolution address your concern?"
   - "Is there anything else we can do?"

8. **Follow-up commitment**
   - "I will personally follow up with you in [timeframe] to ensure this is fully resolved."

**Follow-Up Email:**

```
Subject: RE: [Complaint] - Resolution Summary

Dear [Client Name],

Thank you for taking the time to speak with me today about [issue].
I appreciate your patience and understanding.

Summary of Our Discussion:
[Bullet points from call]

Actions Taken:
1. [Action 1] - Complete ✅
2. [Action 2] - In Progress (ETA: [date])
3. [Action 3] - Scheduled for [date]

Compensation:
[If applicable: service credit, free month, etc.]

Follow-Up:
I will contact you on [date] to confirm that this issue is fully
resolved and that you are satisfied with our response.

If you have any further concerns before then, please don't hesitate
to contact me directly.

Thank you for your continued partnership.

Best regards,
[Manager Name]
Operations Manager
MSP
[Phone] | [Email]
```

---

### Procedure 4: Emergency Client Coordination

#### 4.1 Client IT Coordination During Incidents

**When incident requires client IT involvement:**

Examples:
- Firewall rule changes needed
- VPN credentials expired
- On-premise backup target unreachable
- Client network changes impacting monitoring

**Coordination Template:**

```
Subject: [ACTION REQUIRED] MSP Incident - Client IT Assistance Needed

Dear [Client IT Contact],

We are currently investigating an incident affecting your HIPAA
compliance monitoring and require your assistance to resolve it.

Incident Summary:
- Incident ID: INC-20251031-0042
- Issue: Backup service unable to connect to on-premise backup target
- Impact: Backups not completing (critical SLA breach risk)
- Time Detected: 2025-10-31 14:32 UTC

Technical Details:
Our backup service (running on your management node) is unable to
connect to your backup target at 192.168.10.50:873 (rsync). This
may indicate a firewall change or network routing issue.

Action Needed From Your Team:
1. Verify firewall rules allow outbound rsync (port 873) from management node
2. Verify backup target server is online and accepting connections
3. Test connectivity: ping 192.168.10.50 from management node

We are available to assist with troubleshooting. Please let us know
your availability for a brief call to coordinate.

Contact Information:
- [Engineer Name]: [Phone] (available now)
- [Manager Name]: [Phone] (escalation)

SLA Impact:
We have a 4-hour SLA to resolve critical backup issues. We have
3 hours remaining. Your prompt assistance is greatly appreciated.

Best regards,
[Engineer Name]
MSP Operations Team

Incident ID: INC-20251031-0042
Ticket #: 12345
```

---

#### 4.2 Emergency After-Hours Client Contact

**When to contact client after hours:**

- P0 incident affecting all services
- Security incident (potential breach)
- Data loss risk
- Client approval required for high-risk remediation

**Emergency Contact Protocol:**

1. **Check client emergency contact list**
   - Primary: IT Manager or CTO
   - Secondary: Practice Manager or CEO
   - Tertiary: Designated on-call contact

2. **Attempt contact in order:**
   - Call primary (3 attempts, 5 minutes apart)
   - If no answer, call secondary
   - If no answer, call tertiary
   - If no answer, send SMS and email

3. **Emergency Voicemail Script:**

```
"Hello, this is [Your Name] from MSP calling regarding an urgent
incident affecting your HIPAA compliance monitoring system.

We have detected [brief description of incident] and need to
coordinate with you to resolve it.

This is a [P0/P1] priority incident. Please call me back as soon
as possible at [phone number].

Again, this is [Your Name] from MSP at [phone number].

Thank you."
```

4. **Emergency Email:**

```
Subject: [URGENT] MSP Emergency - Immediate Response Requested

Dear [Client Name],

This is an emergency notification regarding a critical incident
affecting your MSP HIPAA compliance monitoring system.

Incident: [Brief description]
Severity: P0 - Critical
Time Detected: [Timestamp]
Impact: [Description of client impact]

Immediate Action Required:
[What we need from client]

We have been unable to reach you by phone. Please contact us
immediately at:
- [Engineer Name]: [Phone]
- Emergency Hotline: 555-MSP-HELP

We will proceed with standard remediation procedures, but your
input is needed for [specific decision].

This email requires immediate attention.

Best regards,
MSP Operations Team

Incident ID: INC-[ID]
```

---

### Procedure 5: Proactive Client Communication

#### 5.1 Planned Maintenance Notification

**Minimum 7 days advance notice:**

```
Subject: [PLANNED MAINTENANCE] MSP Platform Upgrade - [Date]

Dear Valued Client,

We will be performing planned maintenance on our MSP HIPAA Compliance
Platform on [Date] from [Time] to [Time] UTC ([Local Time]).

Maintenance Window:
- Start: [Date/Time]
- Duration: 2 hours
- End (Estimated): [Date/Time]

Purpose:
[Brief description - e.g., "Upgrading MCP server to improve incident
response performance"]

Expected Impact:
- Compliance dashboard may be temporarily unavailable
- Automated incident response may be delayed by up to 15 minutes
- Evidence bundle generation will backfill automatically after maintenance

No Impact:
✅ Your baseline configuration remains enforced
✅ Local monitoring continues uninterrupted
✅ No action required from your team

If you have any questions or concerns about this maintenance window,
please contact us at ops@msp.com.

Thank you for your patience as we continue to improve our service.

Best regards,
MSP Operations Team
```

---

#### 5.2 Service Degradation Notification

**If platform experiencing performance issues (not outage):**

```
Subject: [SERVICE UPDATE] MSP Platform Performance Degradation

Dear Valued Client,

We are currently experiencing performance degradation affecting our
MSP HIPAA Compliance Platform. We are actively working to resolve
this issue.

Current Status:
- Compliance dashboard response time: Slower than normal
- Automated incident response: Delayed by 5-10 minutes
- Evidence bundle generation: Operating normally

Impact on Your Service:
- Low impact - all services remain operational
- No action required from your team

What We're Doing:
[Brief explanation - e.g., "We are investigating increased traffic
and scaling our infrastructure to handle the load"]

Estimated Resolution:
[Timeframe]

We will provide updates every hour until this is resolved. You can
also check our status page at https://status.msp.com.

If you have any questions, please contact us at ops@msp.com.

We apologize for the inconvenience.

Best regards,
MSP Operations Team
```

---

## Client Satisfaction Metrics

### 5.3 Post-Interaction Surveys

**After every support interaction:**

```
Subject: How did we do? Feedback Request for Ticket #12345

Dear [Client Name],

We recently assisted you with [brief description of issue].

We would greatly appreciate your feedback on this interaction.

Please rate our service (1-5 stars):
[Link to survey]

Questions (optional):
1. How satisfied were you with the resolution?
2. How satisfied were you with our communication?
3. What could we have done better?
4. Any additional comments?

Your feedback helps us continuously improve our service.

Thank you for being a valued client.

Best regards,
MSP Operations Team
```

**Ratings:**
- ⭐⭐⭐⭐⭐ (5 stars) - Excellent
- ⭐⭐⭐⭐ (4 stars) - Good
- ⭐⭐⭐ (3 stars) - Satisfactory
- ⭐⭐ (2 stars) - Needs Improvement (auto-escalate to manager)
- ⭐ (1 star) - Poor (immediate manager call to client)

---

### 5.4 Monthly Client Health Review

**Operations Manager conducts monthly review:**

For each client, review:
- Number of support tickets (trend)
- Average satisfaction rating (target: >4.5)
- SLA compliance (target: 100%)
- Number of complaints (target: 0)
- Client engagement (training requests, questions)

**Proactive Outreach for At-Risk Clients:**

Criteria for "at-risk":
- Satisfaction rating <4.0
- Multiple complaints in past month
- SLA breaches >2 in past month
- No engagement (no questions, no training requests)

**Outreach Template:**

```
Subject: MSP Service Check-In - [Client Name]

Dear [Client Name],

I hope this email finds you well. As your Operations Manager, I like
to periodically check in with our clients to ensure you are getting
the most value from our MSP HIPAA Compliance Platform.

I noticed [specific observation - e.g., "you haven't accessed the
compliance dashboard in the past month"] and wanted to reach out to
see if there's anything we can do to better support you.

I would love to schedule a brief 15-minute call to:
- Answer any questions you may have
- Provide additional training if needed
- Discuss any concerns or feedback
- Ensure our service is meeting your expectations

Are you available for a quick call this week?

Best regards,
[Manager Name]
Operations Manager
MSP
[Phone] | [Email]
```

---

## Escalation Thresholds

| Scenario | Automatic Escalation To | Timeframe |
|----------|------------------------|-----------|
| P0 incident | Operations Manager | Immediate |
| SLA breach imminent | Operations Manager | Before breach |
| Client satisfaction <3 stars | Operations Manager | Same day |
| Client complaint | Operations Manager | Within 2 hours |
| Legal/contract question | Account Executive | Within 4 hours |
| Security incident | Security Officer | Immediate |
| Threat to cancel | Executive Team | Within 1 hour |

---

## Related Documents

- **SOP-001:** Daily Operations
- **SOP-002:** Incident Response
- **SOP-003:** Disaster Recovery
- **SOP-010:** Client Onboarding
- **SOP-011:** Compliance Audit Support
- **EMERG-001:** Service Outage Response
- **EMERG-002:** Data Breach Response

---

## Revision History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2025-10-31 | Initial version | Operations Team |

---

## Training & Acknowledgment

**I have read and understand SOP-004: Client Escalation Procedures**

Operator Name: _________________________
Signature: _________________________
Date: _________________________

Manager Approval: _________________________
Date: _________________________

---

**Document Status:** ✅ Active
**Next Review:** 2025-11-30
**Owner:** Operations Manager
**Classification:** Internal Use Only

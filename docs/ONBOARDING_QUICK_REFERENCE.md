# Client Onboarding: Quick Reference Guide

**Version:** 2.0 | **Updated:** 2025-12-31

**Quick Links:**
- Full SOP: [CLIENT_ONBOARDING_SOP.md](CLIENT_ONBOARDING_SOP.md)
- Templates: `templates/` directory
- Scripts: `/opt/msp/scripts/`

**Production URLs:**
- Dashboard: https://dashboard.osiriscare.net
- API: https://api.osiriscare.net
- Alternate: https://msp.osiriscare.net

---

## 🚀 Onboarding at a Glance

### Timeline
```
Week -1: Pre-Onboarding (4 hours engineer time)
Day 0:   Technical Deployment (3 hours)
Weeks 1-6: Validation Period (2 hours/week)
Ongoing:  Steady-State Support (1 hour/month)
```

### Total Setup Investment
- **Engineer:** ~16 hours over 7 weeks
- **Client:** ~5 hours over 7 weeks

---

## 🖥️ Central Command Dashboard

### Create New Site
1. Go to https://dashboard.osiriscare.net/sites
2. Click **"+ New Site"**
3. Enter clinic name, contact, tier
4. Note the generated `site_id`

### Site Status
- 🟢 Online: Checkin < 5 min
- 🟡 Stale: Checkin 5-15 min
- 🔴 Offline: Checkin > 15 min
- ⚪ Pending: Never connected

### Quick API Commands
```bash
# Create site
curl -X POST https://api.osiriscare.net/api/sites \
  -H "Content-Type: application/json" \
  -d '{"clinic_name": "Test Clinic", "tier": "mid"}'

# Check site status
curl https://api.osiriscare.net/api/sites

# Test appliance checkin
curl -X POST https://api.osiriscare.net/api/appliances/checkin \
  -H "Content-Type: application/json" \
  -d '{"site_id": "xxx", "mac_address": "aa:bb:cc:dd:ee:ff"}'
```

---

## 📋 Phase Breakdown

### Phase 1: Pre-Onboarding (Week -1)

**Day -7: Discovery Call (60 min)**
- Understand client needs
- Assess infrastructure
- Align on service scope

**Day -5: Technical Assessment (90 min)**
- Network discovery
- Server inventory
- Gap analysis
- Deployment planning

**Day -3: Contracts (1 hour)**
- MSA, BAA, SLA signing
- Billing setup
- Contact designation

**Day -1: Pre-Deployment Checklist**
- Terraform workspace ready
- Baseline configured
- Access credentials prepared
- Go/No-Go decision

---

### Phase 2: Technical Deployment (3 hours)

**Hour 1: Infrastructure**
- ✅ Deploy management node (VM or cloud)
- ✅ Apply baseline configuration
- ✅ Run network discovery
- ✅ Deploy monitoring agents

**Hour 2: Configuration**
- ✅ Configure client-specific baseline
- ✅ Set runbook permissions
- ✅ Configure evidence pipeline
- ✅ Set up dashboards & reporting

**Hour 3: Validation**
- ✅ Run synthetic incident tests (4 scenarios)
- ✅ Test backup/restore
- ✅ Client walkthrough
- ✅ Go-live sign-off

---

### Phase 3: Validation Period (6 weeks)

**Week 1:** Daily monitoring & tuning
**Week 2:** First compliance packet review
**Week 3-4:** Incident response validation
**Week 5:** Security hardening verification
**Week 6:** Final audit & go-live

---

### Phase 4: Steady-State (Ongoing)

**Daily:** Automated monitoring (no manual work)
**Weekly:** Executive postcard
**Monthly:** Compliance packet delivery
**Quarterly:** Baseline review

---

## 🎯 Success Criteria

### Deployment Success
- [ ] All infrastructure discovered
- [ ] Baseline enforced (no drift)
- [ ] 4/4 synthetic tests passed
- [ ] Evidence bundles generating
- [ ] Dashboard accessible
- [ ] Client trained

### Validation Success
- [ ] 6 weeks incident-free or auto-resolved
- [ ] First full compliance packet delivered
- [ ] Client satisfied
- [ ] SLA metrics met

### Steady-State Success
- [ ] 99.9%+ uptime
- [ ] <4 hour MTTR (critical)
- [ ] Monthly packets on time
- [ ] Quarterly reviews completed

---

## 🛠️ Key Commands

### Deployment
```bash
# Deploy management node
cd terraform/clients/{client-id}
terraform apply

# Apply baseline
ssh root@mgmt nixos-rebuild switch --flake .#{client-id}

# Run discovery
/opt/msp/scripts/discover-infrastructure.sh --client-id {client-id}

# Test incidents
/opt/msp/scripts/test-incident.sh --client-id {client-id} --type backup_failure
```

### Validation
```bash
# Check agent status
systemctl status msp-watcher

# Verify baseline
nix flake metadata --json | jq .locked.narHash

# Generate test packet
/opt/msp/scripts/generate-compliance-packet.sh --client-id {client-id}

# Verify evidence
cosign verify-blob --key public-key.pem --signature bundle.sig bundle.json
```

### Troubleshooting
```bash
# Restart agent
systemctl restart msp-watcher

# Check logs
journalctl -u msp-watcher -n 100

# Verify connectivity
curl -I https://mcp.msp.internal

# Redeploy agent
/opt/msp/scripts/redeploy-agent.sh --server {server}
```

---

## 📞 Quick Contacts

**Emergency:** 1-800-MSP-HELP (24/7)
**Support:** support@msp.com
**Technical:** engineering@msp.com
**Compliance:** compliance@msp.com

---

## ✅ Pre-Flight Checklist

**Before Starting Deployment:**
- [ ] Discovery call completed
- [ ] Technical assessment done
- [ ] Contracts signed
- [ ] Infrastructure inventory ready
- [ ] Deployment plan approved
- [ ] Client contacts confirmed
- [ ] Terraform workspace created
- [ ] Baseline configuration prepared
- [ ] Backup verified
- [ ] Client IT staff available

**If all ✅ → GO for deployment**
**If any ❌ → Resolve gaps first**

---

## 📊 Typical Client Sizes

### Small (1-5 providers)
- **Servers:** 2-5
- **Deployment:** 2 hours
- **Monthly Cost:** $200-400

### Medium (6-15 providers)
- **Servers:** 6-15
- **Deployment:** 3 hours
- **Monthly Cost:** $600-1200

### Large (15-50 providers)
- **Servers:** 15-30
- **Deployment:** 4 hours
- **Monthly Cost:** $1500-3000

---

## 🎓 Training Materials

**Provided to Client:**
1. Dashboard User Guide (PDF)
2. Incident Response Quick Reference (laminated card)
3. Monthly Packet Usage Guide (PDF)
4. Escalation Procedures (process diagram)

**Training Session:** 30 minutes live demo

---

## 📈 SLA Commitments

| Metric | Target | Measurement |
|--------|--------|-------------|
| Uptime | 99.9% | Monthly |
| Critical MTTR | <4 hours | Per incident |
| High MTTR | <24 hours | Per incident |
| Evidence Delivery | Daily | Nightly job |
| Compliance Packet | Monthly | 1st of month |
| Support Response | <15 min | Emergency |

---

## 💡 Pro Tips

### Deployment Day
- Create site in Central Command first: https://dashboard.osiriscare.net/sites
- Store credentials in Site Detail page before shipping appliance
- Schedule during maintenance window (typically 2-5 AM)
- Have client IT staff on standby (not actively needed, but available)
- Take backup before starting
- Document any deviations from plan

### Week 1
- Monitor site status in dashboard (should show "Online")
- Expect some threshold tuning (normal)
- Daily check-ins build confidence
- Document all adjustments

### Month 1
- First compliance packet is crucial
- Schedule review meeting with client
- Get feedback early

### Ongoing
- Check Sites page for offline appliances
- Quarterly reviews keep relationship strong
- Proactive baseline updates show value
- Monthly packets should be routine

---

## 🚨 Red Flags

**Stop Deployment If:**
- Backup not recent or verified
- Critical systems in unknown state
- Client IT unavailable
- Network access not working
- Contracts not signed

**Escalate During Validation If:**
- >3 critical incidents in Week 1
- Evidence bundles failing consistently
- Client concerns not addressed
- SLA breaches occurring

---

## 📁 Document Structure

```
docs/
├── CLIENT_ONBOARDING_SOP.md          ← Full detailed SOP
├── ONBOARDING_QUICK_REFERENCE.md     ← This document
└── templates/
    ├── discovery-call-notes.md
    ├── technical-assessment-report.md
    ├── deployment-plan.md
    ├── go-live-signoff.docx
    ├── weekly-status-report.md
    └── monthly-compliance-packet.md
```

---

**Created:** 2025-10-31
**Version:** 2.0
**Updated:** 2025-12-31
**For:** MSP Operations Team

**What's New in v2.0:**
- Central Command dashboard (osiriscare.net)
- Sites management and phone-home system
- API commands for automation

**Next:** Read full [CLIENT_ONBOARDING_SOP.md](CLIENT_ONBOARDING_SOP.md) for complete procedures

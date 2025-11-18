# Summary: claude_part1.md

**Main Topics:** Guardrails & Safety, Client Deployment, Network Discovery & Automated Enrollment

**Key Ideas:**
- **Guardrails:** Rate limiting (5-min cooldown per host/tool), parameter validation with Pydantic, whitelist enforcement
- **Client Deployment:** Terraform modules for VM/pod provisioning with cloud-init for automated setup
- **Network Discovery Methods:**
  - Active scanning (nmap for service fingerprinting, SNMP for managed devices, mDNS for IoT)
  - Passive monitoring (ARP/packet capture without active probing)
  - Switch/Router API (query authoritative ARP/MAC tables via SSH)
- **Device Classification:** Automatic tier assignment (Tier 1: infrastructure, Tier 2: applications, Tier 3: medical devices)
- **Automated Enrollment:** Pipeline from discovery → classification → agent deployment → MCP registration
- **HIPAA Safety:** Discovery avoids PHI-bearing ports, minimal footprint, audit trail for all scans

**Repeated Themes:**
- Multi-method discovery for comprehensive asset inventory without manual tracking
- Agent-based monitoring for servers, agentless (SNMP/syslog) for network gear
- Automated vs. manual approval based on device tier
- Switch API discovery is stealthier and more reliable than active scanning

**Code Examples:**
- Python NetworkDiscovery class with nmap/SNMP/mDNS integration
- AutoEnrollment pipeline with agent bootstrapping
- NixOS module for discovery service

import React, { useState } from 'react';
import { GlassCard } from '../components/shared';

type DocSection = 'overview' | 'onboarding' | 'appliance' | 'compliance' | 'portal' | 'troubleshooting';

interface DocItem {
  id: string;
  title: string;
  content: React.ReactNode;
}

const sections: Record<DocSection, { title: string; icon: string; items: DocItem[] }> = {
  overview: {
    title: 'Platform Overview',
    icon: '📋',
    items: [
      {
        id: 'what-is',
        title: 'What is OsirisCare?',
        content: (
          <div className="space-y-4">
            <p>
              OsirisCare is a HIPAA compliance automation platform designed for healthcare SMBs.
              It combines NixOS-based infrastructure, Model Context Protocol (MCP) orchestration,
              and LLM-powered remediation to deliver automated compliance monitoring and self-healing.
            </p>
            <h4 className="font-semibold mt-4">Key Features</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary">
              <li>Automated HIPAA compliance monitoring (8 core controls)</li>
              <li>Three-tier auto-healing (L1 deterministic, L2 LLM, L3 human)</li>
              <li>Immutable audit trail with cryptographic evidence</li>
              <li>Client portal with magic-link access</li>
              <li>Real-time drift detection and remediation</li>
            </ul>
            <h4 className="font-semibold mt-4">Architecture</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary">
              <li><strong>Central Command:</strong> This dashboard - fleet management and monitoring</li>
              <li><strong>MCP Server:</strong> API backend handling check-ins, orders, and evidence</li>
              <li><strong>Compliance Appliance:</strong> NixOS device at client site running checks</li>
              <li><strong>Client Portal:</strong> Read-only compliance dashboard for clients</li>
            </ul>
          </div>
        ),
      },
      {
        id: 'pricing',
        title: 'Pricing Tiers',
        content: (
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h4 className="font-semibold text-accent-primary">Small</h4>
                <p className="text-2xl font-bold mt-2">$200/mo</p>
                <p className="text-sm text-label-tertiary">1-5 providers</p>
                <ul className="mt-3 text-sm space-y-1 text-label-secondary">
                  <li>1 compliance appliance</li>
                  <li>8 core controls</li>
                  <li>Monthly compliance packet</li>
                  <li>Email support</li>
                </ul>
              </div>
              <div className="p-4 bg-accent-primary/10 rounded-ios border border-accent-primary">
                <h4 className="font-semibold text-accent-primary">Mid (Popular)</h4>
                <p className="text-2xl font-bold mt-2">$500/mo</p>
                <p className="text-sm text-label-tertiary">6-15 providers</p>
                <ul className="mt-3 text-sm space-y-1 text-label-secondary">
                  <li>Up to 3 appliances</li>
                  <li>8 core controls + custom</li>
                  <li>Weekly compliance reports</li>
                  <li>Priority support</li>
                </ul>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h4 className="font-semibold text-accent-primary">Large</h4>
                <p className="text-2xl font-bold mt-2">$1,500-3,000/mo</p>
                <p className="text-sm text-label-tertiary">15-50 providers</p>
                <ul className="mt-3 text-sm space-y-1 text-label-secondary">
                  <li>Unlimited appliances</li>
                  <li>Full control customization</li>
                  <li>Daily reports + alerts</li>
                  <li>Dedicated support</li>
                </ul>
              </div>
            </div>
          </div>
        ),
      },
    ],
  },
  onboarding: {
    title: 'Client Onboarding',
    icon: '🚀',
    items: [
      {
        id: 'pipeline',
        title: 'Onboarding Pipeline Stages',
        content: (
          <div className="space-y-4">
            <h4 className="font-semibold">Phase 1: Acquisition (Lead → Shipped)</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3">Stage</th>
                    <th className="text-left py-2 px-3">Description</th>
                    <th className="text-left py-2 px-3">Actions</th>
                    <th className="text-left py-2 px-3">Target</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Lead</td>
                    <td className="py-2 px-3">Initial contact/referral received</td>
                    <td className="py-2 px-3">Log prospect, schedule discovery</td>
                    <td className="py-2 px-3">1-2 days</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Discovery</td>
                    <td className="py-2 px-3">Needs assessment call</td>
                    <td className="py-2 px-3">Document requirements, assess fit</td>
                    <td className="py-2 px-3">1-3 days</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Proposal</td>
                    <td className="py-2 px-3">Pricing and scope presented</td>
                    <td className="py-2 px-3">Send proposal, follow up</td>
                    <td className="py-2 px-3">3-5 days</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Contract</td>
                    <td className="py-2 px-3">Agreement signing</td>
                    <td className="py-2 px-3">Send MSA + BAA, collect signature</td>
                    <td className="py-2 px-3">1-3 days</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Intake</td>
                    <td className="py-2 px-3">Information gathering</td>
                    <td className="py-2 px-3">Collect network info, contacts</td>
                    <td className="py-2 px-3">2-5 days</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Credentials</td>
                    <td className="py-2 px-3">Access provisioned</td>
                    <td className="py-2 px-3">Generate site ID, API keys</td>
                    <td className="py-2 px-3">1 day</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-medium">Shipped</td>
                    <td className="py-2 px-3">Appliance sent</td>
                    <td className="py-2 px-3">Configure appliance, ship</td>
                    <td className="py-2 px-3">1-2 days</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <h4 className="font-semibold mt-6">Phase 2: Activation (Received → Active)</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3">Stage</th>
                    <th className="text-left py-2 px-3">Description</th>
                    <th className="text-left py-2 px-3">Actions</th>
                    <th className="text-left py-2 px-3">Target</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Received</td>
                    <td className="py-2 px-3">Client has appliance</td>
                    <td className="py-2 px-3">Confirm delivery, schedule install</td>
                    <td className="py-2 px-3">1 day</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Connectivity</td>
                    <td className="py-2 px-3">Appliance phoning home</td>
                    <td className="py-2 px-3">Verify check-in, troubleshoot</td>
                    <td className="py-2 px-3">1-2 days</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Scanning</td>
                    <td className="py-2 px-3">Discovery running</td>
                    <td className="py-2 px-3">Monitor scans, review assets</td>
                    <td className="py-2 px-3">1-2 days</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Baseline</td>
                    <td className="py-2 px-3">Baseline established</td>
                    <td className="py-2 px-3">Review baseline, set thresholds</td>
                    <td className="py-2 px-3">1 day</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Compliant</td>
                    <td className="py-2 px-3">All controls passing</td>
                    <td className="py-2 px-3">Verify 8/8 controls, fix issues</td>
                    <td className="py-2 px-3">1-3 days</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-medium">Active</td>
                    <td className="py-2 px-3">Live and monitored</td>
                    <td className="py-2 px-3">Generate portal link, hand off</td>
                    <td className="py-2 px-3">-</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        ),
      },
      {
        id: 'checklist',
        title: 'Onboarding Checklist',
        content: (
          <div className="space-y-4">
            <h4 className="font-semibold">Pre-Onboarding</h4>
            <ul className="space-y-2">
              {[
                'Signed MSA and BAA received',
                'Payment method on file',
                'Primary contact identified',
                'Network diagram or description obtained',
                'Current IT provider contact (if applicable)',
              ].map((item, i) => (
                <li key={i} className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span className="text-label-secondary">{item}</span>
                </li>
              ))}
            </ul>

            <h4 className="font-semibold mt-6">Appliance Setup</h4>
            <ul className="space-y-2">
              {[
                'Site ID generated in Central Command',
                'API key created and secured',
                'Appliance flashed with client config',
                'Appliance tested in lab (check-in verified)',
                'Tracking number sent to client',
              ].map((item, i) => (
                <li key={i} className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span className="text-label-secondary">{item}</span>
                </li>
              ))}
            </ul>

            <h4 className="font-semibold mt-6">Activation</h4>
            <ul className="space-y-2">
              {[
                'Appliance receiving check-ins',
                'Network scan completed',
                'Baseline configuration saved',
                'All 8 controls passing',
                'Portal magic link generated and sent',
                'Client orientation call completed',
              ].map((item, i) => (
                <li key={i} className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span className="text-label-secondary">{item}</span>
                </li>
              ))}
            </ul>
          </div>
        ),
      },
    ],
  },
  appliance: {
    title: 'Appliance Setup',
    icon: '🖥️',
    items: [
      {
        id: 'hardware',
        title: 'Hardware Requirements',
        content: (
          <div className="space-y-4">
            <h4 className="font-semibold">Minimum Specifications</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary">
              <li>Intel N100 or equivalent (4 cores, 2.0+ GHz)</li>
              <li>8GB RAM minimum, 16GB recommended</li>
              <li>128GB NVMe SSD (LUKS encrypted)</li>
              <li>2x Gigabit Ethernet ports</li>
              <li>TPM 2.0 (optional, for secure boot)</li>
            </ul>

            <h4 className="font-semibold mt-6">Recommended Hardware</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <p className="font-medium">Beelink S12 Pro Mini PC</p>
              <ul className="mt-2 text-sm text-label-secondary space-y-1">
                <li>Intel N100 (4C/4T, 3.4GHz boost)</li>
                <li>16GB DDR5 RAM</li>
                <li>500GB NVMe SSD</li>
                <li>Dual 2.5GbE NICs</li>
                <li>~$200 retail</li>
              </ul>
            </div>

            <h4 className="font-semibold mt-6">Network Requirements</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary">
              <li>Static IP or DHCP reservation</li>
              <li>Outbound HTTPS (443) to api.osiriscare.net</li>
              <li>LAN access to monitored devices</li>
              <li>No inbound ports required</li>
            </ul>
          </div>
        ),
      },
      {
        id: 'flashing',
        title: 'Flashing the Appliance',
        content: (
          <div className="space-y-4">
            <h4 className="font-semibold">Step 1: Generate Configuration</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm">
              <pre>{`# In Central Command, create the site first
# Then generate the appliance config:

nix build .#appliance-image \\
  --argstr siteId "clinic-name-abc123" \\
  --argstr apiKey "sk_live_..." \\
  --argstr apiUrl "https://api.osiriscare.net"`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Step 2: Write to USB</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm">
              <pre>{`# Find USB device
lsblk

# Write image (replace /dev/sdX)
sudo dd if=result/nixos.img of=/dev/sdX \\
  bs=4M status=progress conv=fsync`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Step 3: Boot and Verify</h4>
            <ol className="list-decimal list-inside space-y-2 text-label-secondary">
              <li>Insert USB into target device, power on</li>
              <li>Wait for NixOS to boot (2-3 minutes)</li>
              <li>Appliance will auto-connect via DHCP</li>
              <li>Check Central Command for check-in within 5 minutes</li>
              <li>If no check-in, verify network and check logs</li>
            </ol>
          </div>
        ),
      },
      {
        id: 'troubleshooting-appliance',
        title: 'Appliance Troubleshooting',
        content: (
          <div className="space-y-4">
            <h4 className="font-semibold">No Check-In After Boot</h4>
            <ol className="list-decimal list-inside space-y-2 text-label-secondary">
              <li>Verify Ethernet cable is connected (check link lights)</li>
              <li>Confirm DHCP is working: <code className="bg-fill-secondary px-1 rounded">journalctl -u systemd-networkd</code></li>
              <li>Test DNS: <code className="bg-fill-secondary px-1 rounded">ping api.osiriscare.net</code></li>
              <li>Check agent logs: <code className="bg-fill-secondary px-1 rounded">journalctl -u compliance-agent</code></li>
              <li>Verify API key is correct in <code className="bg-fill-secondary px-1 rounded">/etc/compliance-agent/config.yaml</code></li>
            </ol>

            <h4 className="font-semibold mt-6">Connectivity Drops</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary">
              <li>Check for firewall blocking outbound 443</li>
              <li>Verify static IP hasn't changed</li>
              <li>Review <code className="bg-fill-secondary px-1 rounded">/var/log/mcp-client.log</code></li>
              <li>Restart agent: <code className="bg-fill-secondary px-1 rounded">systemctl restart compliance-agent</code></li>
            </ul>

            <h4 className="font-semibold mt-6">Remote Access (Emergency)</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm">
              <pre>{`# SSH is enabled for admin user
ssh admin@<appliance-ip>

# View real-time logs
journalctl -u compliance-agent -f

# Manual check-in
/opt/compliance-agent/bin/phone-home --now`}</pre>
            </div>
          </div>
        ),
      },
    ],
  },
  compliance: {
    title: 'Compliance Controls',
    icon: '🛡️',
    items: [
      {
        id: 'controls',
        title: '8 Core HIPAA Controls',
        content: (
          <div className="space-y-4">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3">Control</th>
                    <th className="text-left py-2 px-3">Description</th>
                    <th className="text-left py-2 px-3">HIPAA Reference</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Endpoint Drift</td>
                    <td className="py-2 px-3">Configuration matches approved baseline</td>
                    <td className="py-2 px-3">164.308(a)(1)(ii)(D), 164.310(d)(1)</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Patch Freshness</td>
                    <td className="py-2 px-3">Critical patches applied within 72 hours</td>
                    <td className="py-2 px-3">164.308(a)(5)(ii)(B)</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Backup Success</td>
                    <td className="py-2 px-3">Daily backups + monthly restore test</td>
                    <td className="py-2 px-3">164.308(a)(7)(ii)(A), 164.310(d)(2)(iv)</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">MFA Coverage</td>
                    <td className="py-2 px-3">MFA required for all human accounts</td>
                    <td className="py-2 px-3">164.312(a)(2)(i), 164.308(a)(4)(ii)(C)</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Privileged Access</td>
                    <td className="py-2 px-3">Admin accounts reviewed and limited</td>
                    <td className="py-2 px-3">164.308(a)(3)(ii)(B), 164.308(a)(4)(ii)(B)</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Git Protections</td>
                    <td className="py-2 px-3">Config tracked in version control</td>
                    <td className="py-2 px-3">164.312(b), 164.308(a)(5)(ii)(D)</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-medium">Secrets Hygiene</td>
                    <td className="py-2 px-3">No plaintext credentials in files</td>
                    <td className="py-2 px-3">164.312(a)(2)(i), 164.308(a)(4)(ii)(B)</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-medium">Storage Posture</td>
                    <td className="py-2 px-3">Encryption at rest + proper permissions</td>
                    <td className="py-2 px-3">164.310(d)(2)(iii), 164.312(a)(1)</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        ),
      },
      {
        id: 'auto-healing',
        title: 'Three-Tier Auto-Healing',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-fill-secondary rounded-ios">
              <h4 className="font-semibold text-level-l1">L1: Deterministic (70-80%)</h4>
              <p className="text-sm text-label-secondary mt-1">
                YAML-based rules executed in &lt;100ms at zero cost. Handles known issues
                like service restarts, log rotation, and configuration resets.
              </p>
            </div>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <h4 className="font-semibold text-level-l2">L2: LLM Planner (15-20%)</h4>
              <p className="text-sm text-label-secondary mt-1">
                GPT-4 powered analysis for novel issues. Generates remediation plans
                in 2-5 seconds at ~$0.001 per incident. Successful patterns promote to L1.
              </p>
            </div>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <h4 className="font-semibold text-level-l3">L3: Human Escalation (5-10%)</h4>
              <p className="text-sm text-label-secondary mt-1">
                Complex or sensitive issues requiring human judgment. Creates tickets
                with full context and recommended actions.
              </p>
            </div>
          </div>
        ),
      },
    ],
  },
  portal: {
    title: 'Client Portal',
    icon: '🌐',
    items: [
      {
        id: 'generating-links',
        title: 'Generating Portal Links',
        content: (
          <div className="space-y-4">
            <p className="text-label-secondary">
              Portal links are magic URLs that give clients read-only access to their
              compliance dashboard. No login required - the token in the URL authenticates access.
            </p>

            <h4 className="font-semibold mt-4">Via Central Command</h4>
            <ol className="list-decimal list-inside space-y-2 text-label-secondary">
              <li>Navigate to Sites → [Client Name]</li>
              <li>Click "Generate Portal Link" button</li>
              <li>Copy the URL and send to client</li>
            </ol>

            <h4 className="font-semibold mt-4">Via API</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm">
              <pre>{`curl -X POST https://api.osiriscare.net/api/portal/sites/{site_id}/generate-token

# Response:
{
  "portal_url": "https://portal.osiriscare.net/portal/site/{site_id}?token=...",
  "token": "YBnJGHrJS5151NC-...",
  "expires": "never"
}`}</pre>
            </div>

            <h4 className="font-semibold mt-4">Portal Features</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary">
              <li>Real-time compliance score and KPIs</li>
              <li>8 control status tiles with HIPAA mappings</li>
              <li>Recent incident log with auto-fix status</li>
              <li>Evidence bundle downloads</li>
              <li>Monthly compliance packet (PDF)</li>
            </ul>
          </div>
        ),
      },
    ],
  },
  troubleshooting: {
    title: 'Troubleshooting',
    icon: '🔧',
    items: [
      {
        id: 'common-issues',
        title: 'Common Issues',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-health-critical/10 border-l-4 border-health-critical rounded-r-ios">
              <h4 className="font-semibold text-health-critical">Appliance Not Checking In</h4>
              <ol className="mt-2 list-decimal list-inside text-sm text-label-secondary space-y-1">
                <li>Verify network connectivity (ping api.osiriscare.net)</li>
                <li>Check firewall allows outbound HTTPS (443)</li>
                <li>Verify API key in config matches Central Command</li>
                <li>Review agent logs: journalctl -u compliance-agent</li>
                <li>Restart agent: systemctl restart compliance-agent</li>
              </ol>
            </div>

            <div className="p-4 bg-health-warning/10 border-l-4 border-health-warning rounded-r-ios">
              <h4 className="font-semibold text-health-warning">Control Failing Unexpectedly</h4>
              <ol className="mt-2 list-decimal list-inside text-sm text-label-secondary space-y-1">
                <li>Check control details for specific failure reason</li>
                <li>Verify baseline configuration is current</li>
                <li>Review recent changes in git log</li>
                <li>Check if exception should be applied</li>
                <li>Contact support if issue persists</li>
              </ol>
            </div>

            <div className="p-4 bg-fill-secondary border-l-4 border-separator-light rounded-r-ios">
              <h4 className="font-semibold">Portal Link Not Working</h4>
              <ol className="mt-2 list-decimal list-inside text-sm text-label-secondary space-y-1">
                <li>Verify URL is complete (token parameter present)</li>
                <li>Generate a new link if token may be compromised</li>
                <li>Check that site exists and is active</li>
                <li>Try accessing directly via portal.osiriscare.net</li>
              </ol>
            </div>
          </div>
        ),
      },
      {
        id: 'support',
        title: 'Getting Support',
        content: (
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h4 className="font-semibold">Email Support</h4>
                <p className="text-sm text-label-secondary mt-1">
                  support@osiriscare.net
                </p>
                <p className="text-xs text-label-tertiary mt-2">Response within 24 hours</p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h4 className="font-semibold">Emergency Line</h4>
                <p className="text-sm text-label-secondary mt-1">
                  (570) 555-0123
                </p>
                <p className="text-xs text-label-tertiary mt-2">Critical issues only</p>
              </div>
            </div>

            <h4 className="font-semibold mt-4">When Contacting Support</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary">
              <li>Include Site ID and appliance hostname</li>
              <li>Describe the issue and when it started</li>
              <li>Attach relevant log excerpts</li>
              <li>Note any recent changes made</li>
            </ul>
          </div>
        ),
      },
    ],
  },
};

export const Documentation: React.FC = () => {
  const [activeSection, setActiveSection] = useState<DocSection>('overview');
  const [activeItem, setActiveItem] = useState<string>(sections.overview.items[0].id);

  const currentSection = sections[activeSection];
  const currentItem = currentSection.items.find((item) => item.id === activeItem) || currentSection.items[0];

  return (
    <div className="flex gap-6 min-h-[calc(100vh-200px)]">
      {/* Sidebar Navigation */}
      <div className="w-64 flex-shrink-0">
        <GlassCard className="sticky top-6">
          <h2 className="text-lg font-semibold mb-4">Documentation</h2>
          <nav className="space-y-1">
            {(Object.entries(sections) as [DocSection, typeof sections[DocSection]][]).map(([key, section]) => (
              <div key={key}>
                <button
                  onClick={() => {
                    setActiveSection(key);
                    setActiveItem(section.items[0].id);
                  }}
                  className={`w-full text-left px-3 py-2 rounded-ios text-sm font-medium transition-colors flex items-center gap-2 ${
                    activeSection === key
                      ? 'bg-accent-primary text-white'
                      : 'text-label-secondary hover:bg-fill-secondary'
                  }`}
                >
                  <span>{section.icon}</span>
                  {section.title}
                </button>
                {activeSection === key && (
                  <div className="ml-6 mt-1 space-y-1">
                    {section.items.map((item) => (
                      <button
                        key={item.id}
                        onClick={() => setActiveItem(item.id)}
                        className={`w-full text-left px-3 py-1.5 rounded-ios-sm text-xs transition-colors ${
                          activeItem === item.id
                            ? 'bg-accent-primary/20 text-accent-primary'
                            : 'text-label-tertiary hover:text-label-secondary'
                        }`}
                      >
                        {item.title}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </nav>
        </GlassCard>
      </div>

      {/* Content Area */}
      <div className="flex-1">
        <GlassCard>
          <div className="flex items-center gap-2 mb-4">
            <span className="text-2xl">{currentSection.icon}</span>
            <div>
              <p className="text-xs text-label-tertiary uppercase tracking-wide">
                {currentSection.title}
              </p>
              <h1 className="text-xl font-semibold">{currentItem.title}</h1>
            </div>
          </div>
          <div className="prose prose-sm max-w-none text-label-primary">
            {currentItem.content}
          </div>
        </GlassCard>
      </div>
    </div>
  );
};

export default Documentation;

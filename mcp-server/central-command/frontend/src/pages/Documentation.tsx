import React, { useState } from 'react';
import { GlassCard } from '../components/shared';

type DocSection = 'overview' | 'operations' | 'onboarding' | 'appliance' | 'compliance' | 'portal' | 'troubleshooting';

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
  operations: {
    title: 'Operations & SOPs',
    icon: '📖',
    items: [
      {
        id: 'daily-ops',
        title: 'Daily Operations Checklist',
        content: (
          <div className="space-y-4">
            <p className="text-label-secondary">
              Complete these tasks each business day to maintain fleet health and catch issues early.
            </p>

            <h4 className="font-semibold">Morning Check (9:00 AM)</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ul className="space-y-2">
                {[
                  'Review Central Command dashboard for offline appliances',
                  'Check for any L3 escalations requiring human intervention',
                  'Verify overnight backup jobs completed successfully',
                  'Review any new incidents from overnight auto-healing',
                  'Check email for client-reported issues',
                ].map((item, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="text-accent-primary font-mono text-sm">{i + 1}.</span>
                    <span className="text-label-secondary text-sm">{item}</span>
                  </li>
                ))}
              </ul>
            </div>

            <h4 className="font-semibold mt-6">Afternoon Check (3:00 PM)</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ul className="space-y-2">
                {[
                  'Review compliance scores - investigate any drops below 90%',
                  'Check patch status for critical vulnerabilities',
                  'Follow up on any pending client requests',
                  'Review pipeline stages - move clients through stages',
                  'Document any notable incidents in session log',
                ].map((item, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="text-accent-primary font-mono text-sm">{i + 1}.</span>
                    <span className="text-label-secondary text-sm">{item}</span>
                  </li>
                ))}
              </ul>
            </div>

            <h4 className="font-semibold mt-6">Weekly Tasks (Fridays)</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ul className="space-y-2">
                {[
                  'Generate weekly compliance reports for all active sites',
                  'Review L1 rule effectiveness - promote any new patterns',
                  'Check for appliance firmware/NixOS updates',
                  'Backup Central Command database',
                  'Review and close resolved incidents',
                  'Update client portal links if needed',
                ].map((item, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="text-accent-primary font-mono text-sm">{i + 1}.</span>
                    <span className="text-label-secondary text-sm">{item}</span>
                  </li>
                ))}
              </ul>
            </div>

            <h4 className="font-semibold mt-6">Monthly Tasks (1st of Month)</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ul className="space-y-2">
                {[
                  'Generate monthly compliance packets for all clients',
                  'Review and update baseline configurations',
                  'Audit privileged access across all sites',
                  'Test backup restore procedures (random site selection)',
                  'Review billing and ensure all active sites invoiced',
                  'Update documentation with any process changes',
                ].map((item, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="text-accent-primary font-mono text-sm">{i + 1}.</span>
                    <span className="text-label-secondary text-sm">{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ),
      },
      {
        id: 'new-clinic-sop',
        title: 'SOP: Onboard New Clinic',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-accent-primary/10 border-l-4 border-accent-primary rounded-r-ios">
              <p className="text-sm font-medium">End-to-end procedure for onboarding a new clinic from signed contract to active monitoring.</p>
              <p className="text-xs text-label-tertiary mt-1">Estimated time: 2-3 hours hands-on over 5-7 days</p>
            </div>

            <h4 className="font-semibold">Phase 1: Pre-Deployment (Day 1-2)</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3 w-12">#</th>
                    <th className="text-left py-2 px-3">Task</th>
                    <th className="text-left py-2 px-3">Details</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">1</td>
                    <td className="py-2 px-3 font-medium">Collect signed documents</td>
                    <td className="py-2 px-3">MSA + BAA must be signed. Store in client folder.</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">2</td>
                    <td className="py-2 px-3 font-medium">Create site in Central Command</td>
                    <td className="py-2 px-3">Sites → Add Site. Use format: <code className="bg-fill-tertiary px-1 rounded">clinic-name-XXXX</code></td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">3</td>
                    <td className="py-2 px-3 font-medium">Generate API credentials</td>
                    <td className="py-2 px-3">Site Settings → Generate API Key. Save securely.</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">4</td>
                    <td className="py-2 px-3 font-medium">Gather network info</td>
                    <td className="py-2 px-3">IP range, DHCP/static, firewall rules, existing IT contact.</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">5</td>
                    <td className="py-2 px-3 font-medium">Provision appliance config</td>
                    <td className="py-2 px-3">Run generate-config.py with site ID. See "SOP: Image Appliance".</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-mono">6</td>
                    <td className="py-2 px-3 font-medium">Flash and test appliance</td>
                    <td className="py-2 px-3">Write image to USB, boot in lab, verify check-in.</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <h4 className="font-semibold mt-6">Phase 2: Shipping (Day 2-3)</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3 w-12">#</th>
                    <th className="text-left py-2 px-3">Task</th>
                    <th className="text-left py-2 px-3">Details</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">7</td>
                    <td className="py-2 px-3 font-medium">Package appliance</td>
                    <td className="py-2 px-3">Include: appliance, power cable, Ethernet cable, quick start card.</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">8</td>
                    <td className="py-2 px-3 font-medium">Ship with tracking</td>
                    <td className="py-2 px-3">Use insured shipping. Update pipeline stage to "Shipped".</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-mono">9</td>
                    <td className="py-2 px-3 font-medium">Send tracking + instructions</td>
                    <td className="py-2 px-3">Email client with tracking number and installation PDF.</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <h4 className="font-semibold mt-6">Phase 3: Activation (Day 4-7)</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3 w-12">#</th>
                    <th className="text-left py-2 px-3">Task</th>
                    <th className="text-left py-2 px-3">Details</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">10</td>
                    <td className="py-2 px-3 font-medium">Confirm delivery</td>
                    <td className="py-2 px-3">Call/email client when delivered. Update to "Received".</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">11</td>
                    <td className="py-2 px-3 font-medium">Schedule install call</td>
                    <td className="py-2 px-3">15-30 min call while client plugs in appliance.</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">12</td>
                    <td className="py-2 px-3 font-medium">Verify connectivity</td>
                    <td className="py-2 px-3">Watch Central Command for check-in. Update to "Connectivity".</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">13</td>
                    <td className="py-2 px-3 font-medium">Monitor initial scan</td>
                    <td className="py-2 px-3">Wait for network discovery to complete. Update to "Scanning".</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">14</td>
                    <td className="py-2 px-3 font-medium">Establish baseline</td>
                    <td className="py-2 px-3">Review discovered assets, set baseline. Update to "Baseline".</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">15</td>
                    <td className="py-2 px-3 font-medium">Achieve compliance</td>
                    <td className="py-2 px-3">Fix any failing controls. Target 8/8 passing. Update to "Compliant".</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">16</td>
                    <td className="py-2 px-3 font-medium">Generate portal link</td>
                    <td className="py-2 px-3">Create magic link, send to client with orientation doc.</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-mono">17</td>
                    <td className="py-2 px-3 font-medium">Orientation call</td>
                    <td className="py-2 px-3">Walk client through portal. Answer questions. Mark "Active".</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        ),
      },
      {
        id: 'image-appliance-sop',
        title: 'SOP: Image Appliance',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-accent-primary/10 border-l-4 border-accent-primary rounded-r-ios">
              <p className="text-sm font-medium">Build and flash a compliance appliance for deployment.</p>
              <p className="text-xs text-label-tertiary mt-1">Requires: Linux workstation, USB drive (8GB+), target hardware</p>
            </div>

            <h4 className="font-semibold">Prerequisites</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary text-sm">
              <li>Site created in Central Command with API key</li>
              <li>Nix installed on build machine</li>
              <li>Target hardware: HP T640 or equivalent (4GB+ RAM)</li>
              <li>USB drive 8GB+ (will be erased)</li>
            </ul>

            <h4 className="font-semibold mt-6">Step 1: Generate Site Configuration</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`cd /path/to/Msp_Flakes

# Generate config for the site
python iso/provisioning/generate-config.py \\
  --site-id "clinic-smith-abc123" \\
  --site-name "Smith Family Practice" \\
  --timezone "America/New_York"

# Output:
# ./appliance-config/clinic-smith-abc123/
# ├── config.yaml        # Main configuration
# ├── certs/             # mTLS certificates
# │   ├── client.crt
# │   ├── client.key
# │   └── ca.crt
# └── registration.yaml  # For Central Command`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Step 2: Register Site in Central Command</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Copy the API key from registration.yaml
cat appliance-config/clinic-smith-abc123/registration.yaml

# Add to Central Command via API or UI
curl -X POST https://api.osiriscare.net/api/sites \\
  -H "Authorization: Bearer $ADMIN_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "site_id": "clinic-smith-abc123",
    "site_name": "Smith Family Practice",
    "api_key": "sk-site-..."
  }'`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Step 3: Build the ISO</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Build the appliance ISO (requires Linux or remote builder)
nix build -f flake-compliance.nix appliance-iso -o result-iso

# ISO will be at:
ls -lh result-iso/iso/osiriscare-appliance.iso
# -r--r--r-- 1 user user 850M Dec 31 12:00 osiriscare-appliance.iso`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Step 4: Flash to USB</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Find USB device (CAREFUL - this erases the drive!)
lsblk
# Example: /dev/sdc

# Write ISO to USB
sudo dd if=result-iso/iso/osiriscare-appliance.iso \\
  of=/dev/sdX \\
  bs=4M \\
  status=progress \\
  conv=fsync

# Verify
sync`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Step 5: Copy Configuration to USB</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Mount the USB config partition
sudo mkdir -p /mnt/usb
sudo mount /dev/sdX2 /mnt/usb

# Copy site configuration
sudo mkdir -p /mnt/usb/msp
sudo cp appliance-config/clinic-smith-abc123/config.yaml /mnt/usb/msp/
sudo cp -r appliance-config/clinic-smith-abc123/certs /mnt/usb/msp/

# Unmount
sudo umount /mnt/usb`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Step 6: Test in Lab</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ol className="list-decimal list-inside space-y-2 text-label-secondary text-sm">
                <li>Boot target hardware from USB</li>
                <li>Wait for NixOS to boot (2-3 minutes)</li>
                <li>Verify network connectivity via status page (port 80)</li>
                <li>Check Central Command for check-in within 5 minutes</li>
                <li>Verify all 8 controls reporting (may show warnings initially)</li>
                <li>Label appliance with site ID</li>
              </ol>
            </div>

            <div className="p-4 bg-health-warning/10 border-l-4 border-health-warning rounded-r-ios mt-4">
              <h4 className="font-semibold text-health-warning">Troubleshooting</h4>
              <ul className="mt-2 text-sm text-label-secondary space-y-1">
                <li><strong>No boot:</strong> Check BIOS boot order, ensure UEFI mode</li>
                <li><strong>No network:</strong> Verify Ethernet cable, check DHCP</li>
                <li><strong>No check-in:</strong> Verify config.yaml API key matches Central Command</li>
              </ul>
            </div>
          </div>
        ),
      },
      {
        id: 'provision-site-sop',
        title: 'SOP: Provision Site Credentials',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-accent-primary/10 border-l-4 border-accent-primary rounded-r-ios">
              <p className="text-sm font-medium">Generate all credentials needed for a new site deployment.</p>
              <p className="text-xs text-label-tertiary mt-1">Run before imaging appliance</p>
            </div>

            <h4 className="font-semibold">What Gets Generated</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary text-sm">
              <li><strong>Site ID:</strong> Unique identifier (format: clinic-name-XXXX)</li>
              <li><strong>API Key:</strong> For appliance authentication to Central Command</li>
              <li><strong>Portal Token:</strong> Magic link token for client dashboard</li>
              <li><strong>mTLS Certificates:</strong> Client cert for mutual TLS</li>
            </ul>

            <h4 className="font-semibold mt-6">Via Central Command UI</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ol className="list-decimal list-inside space-y-2 text-label-secondary text-sm">
                <li>Navigate to <strong>Sites → Add Site</strong></li>
                <li>Enter site name (e.g., "Smith Family Practice")</li>
                <li>Select pricing tier</li>
                <li>Click <strong>Create Site</strong></li>
                <li>Copy the generated Site ID and API Key</li>
                <li>Download the configuration bundle (.zip)</li>
              </ol>
            </div>

            <h4 className="font-semibold mt-6">Via Command Line</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Run the provisioning script
python iso/provisioning/generate-config.py \\
  --site-id "clinic-smith-abc123" \\
  --site-name "Smith Family Practice" \\
  --timezone "America/New_York" \\
  --output-dir ./appliance-config

# Output structure:
./appliance-config/clinic-smith-abc123/
├── config.yaml           # Appliance config
├── certs/
│   ├── client.crt        # mTLS client cert
│   ├── client.key        # mTLS private key (SECURE!)
│   └── ca.crt            # CA certificate
└── registration.yaml     # Central Command registration

# View generated credentials
cat registration.yaml
# site_id: clinic-smith-abc123
# api_key: sk-site-xxxxxxxx
# portal_token: YBnJGHrJS5151NC...
# portal_url: https://portal.osiriscare.net/...`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Register in Central Command</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Add site via API
curl -X POST https://api.osiriscare.net/api/sites \\
  -H "Authorization: Bearer $ADMIN_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d @registration.yaml

# Or manually add in UI:
# 1. Sites → Add Site
# 2. Enter site_id and api_key from registration.yaml
# 3. Save`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Security Notes</h4>
            <div className="p-4 bg-health-critical/10 border-l-4 border-health-critical rounded-r-ios">
              <ul className="text-sm text-label-secondary space-y-1">
                <li><strong>client.key</strong> - Never share or commit to git</li>
                <li><strong>api_key</strong> - Rotate if compromised</li>
                <li><strong>portal_token</strong> - Can be regenerated if needed</li>
                <li>Store config bundles encrypted when not in use</li>
              </ul>
            </div>
          </div>
        ),
      },
      {
        id: 'replace-appliance-sop',
        title: 'SOP: Replace Failed Appliance',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-accent-primary/10 border-l-4 border-accent-primary rounded-r-ios">
              <p className="text-sm font-medium">Replace a failed or damaged appliance at an existing site.</p>
              <p className="text-xs text-label-tertiary mt-1">Preserves site credentials and compliance history</p>
            </div>

            <h4 className="font-semibold">When to Replace</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary text-sm">
              <li>Hardware failure (won't boot, storage failure)</li>
              <li>Appliance damaged in shipping/handling</li>
              <li>Upgrade to newer hardware model</li>
              <li>Security incident requiring device wipe</li>
            </ul>

            <h4 className="font-semibold mt-6">Procedure</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3 w-12">#</th>
                    <th className="text-left py-2 px-3">Task</th>
                    <th className="text-left py-2 px-3">Notes</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">1</td>
                    <td className="py-2 px-3 font-medium">Retrieve existing config</td>
                    <td className="py-2 px-3">Download from Central Command or use backup</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">2</td>
                    <td className="py-2 px-3 font-medium">Flash new appliance</td>
                    <td className="py-2 px-3">Same ISO, same config.yaml and certs</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">3</td>
                    <td className="py-2 px-3 font-medium">Test in lab</td>
                    <td className="py-2 px-3">Verify check-in with existing site ID</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">4</td>
                    <td className="py-2 px-3 font-medium">Ship replacement</td>
                    <td className="py-2 px-3">Include return label for failed unit</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">5</td>
                    <td className="py-2 px-3 font-medium">Coordinate swap</td>
                    <td className="py-2 px-3">Client unplugs old, plugs in new</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">6</td>
                    <td className="py-2 px-3 font-medium">Verify connectivity</td>
                    <td className="py-2 px-3">Confirm check-ins resume in Central Command</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-mono">7</td>
                    <td className="py-2 px-3 font-medium">Dispose old unit</td>
                    <td className="py-2 px-3">Wipe storage, recycle hardware per policy</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="p-4 bg-fill-secondary rounded-ios mt-4">
              <h4 className="font-semibold text-sm">Key Point</h4>
              <p className="text-sm text-label-secondary mt-1">
                The site ID and API key remain unchanged. The new appliance simply continues
                where the old one left off. Compliance history is preserved in Central Command.
              </p>
            </div>
          </div>
        ),
      },
      {
        id: 'offboard-clinic-sop',
        title: 'SOP: Offboard Clinic',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-health-warning/10 border-l-4 border-health-warning rounded-r-ios">
              <p className="text-sm font-medium">Properly terminate service and preserve records for compliance.</p>
              <p className="text-xs text-label-tertiary mt-1">HIPAA requires retention of records for 6 years</p>
            </div>

            <h4 className="font-semibold">Pre-Offboarding</h4>
            <ul className="space-y-2">
              {[
                'Confirm cancellation request in writing',
                'Verify final invoice is paid',
                'Schedule offboarding date with client',
                'Export final compliance report',
              ].map((item, i) => (
                <li key={i} className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span className="text-label-secondary text-sm">{item}</span>
                </li>
              ))}
            </ul>

            <h4 className="font-semibold mt-6">Offboarding Tasks</h4>
            <ul className="space-y-2">
              {[
                'Generate final compliance packet (PDF)',
                'Export all evidence bundles to archive',
                'Revoke API key in Central Command',
                'Invalidate portal magic link',
                'Mark site as "Churned" in pipeline',
                'Archive site data (do not delete - 6 year retention)',
                'Request appliance return (prepaid label)',
              ].map((item, i) => (
                <li key={i} className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span className="text-label-secondary text-sm">{item}</span>
                </li>
              ))}
            </ul>

            <h4 className="font-semibold mt-6">Post-Offboarding</h4>
            <ul className="space-y-2">
              {[
                'Wipe returned appliance',
                'Update inventory records',
                'Document reason for churn',
                'Schedule 90-day follow-up (win-back opportunity)',
              ].map((item, i) => (
                <li key={i} className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span className="text-label-secondary text-sm">{item}</span>
                </li>
              ))}
            </ul>

            <h4 className="font-semibold mt-6">Archive Commands</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Export site data before archiving
curl https://api.osiriscare.net/api/sites/{site_id}/export \\
  -H "Authorization: Bearer $ADMIN_TOKEN" \\
  -o clinic-smith-export.tar.gz

# Archive contains:
# - All evidence bundles
# - Compliance history
# - Incident logs
# - Configuration snapshots

# Mark site as churned (preserves data, stops billing)
curl -X PATCH https://api.osiriscare.net/api/sites/{site_id} \\
  -H "Authorization: Bearer $ADMIN_TOKEN" \\
  -d '{"status": "churned", "churn_date": "2025-01-01"}'`}</pre>
            </div>
          </div>
        ),
      },
      {
        id: 'auto-provision-sop',
        title: 'SOP: Zero-Touch Appliance Provisioning',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-accent-primary/10 border-l-4 border-accent-primary rounded-r-ios">
              <p className="text-sm font-medium">Configure appliances to auto-provision on first boot with zero manual config.</p>
              <p className="text-xs text-label-tertiary mt-1">New in v9 - Eliminates manual SSH config at client sites</p>
            </div>

            <h4 className="font-semibold">Two Provisioning Methods</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold text-accent-primary">Option 1: USB Config File</h5>
                <p className="text-xs text-label-secondary mt-1">
                  Place config.yaml on USB drive. Appliance reads it on boot.
                </p>
                <p className="text-xs text-label-tertiary mt-2">Best for: Pre-configured shipments</p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold text-accent-primary">Option 4: MAC-Based Lookup</h5>
                <p className="text-xs text-label-secondary mt-1">
                  Register MAC address in Central Command. Appliance fetches config via API.
                </p>
                <p className="text-xs text-label-tertiary mt-2">Best for: Pre-registered hardware</p>
              </div>
            </div>

            <h4 className="font-semibold mt-6">Method 1: USB Configuration</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# 1. Generate config for the site
python iso/provisioning/generate-config.py \\
  --site-id "clinic-smith-abc123" \\
  --site-name "Smith Family Practice"

# 2. Copy config to USB drive root
# The appliance checks these locations on boot:
#   /config.yaml
#   /msp/config.yaml
#   /osiriscare/config.yaml
#   /MSP/config.yaml

cp appliance-config/clinic-smith-abc123/config.yaml /Volumes/USB/config.yaml

# 3. Insert USB into appliance before first boot
# Config is copied to /var/lib/msp/config.yaml automatically`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Method 4: MAC-Based Provisioning</h4>
            <div className="p-4 bg-fill-secondary rounded-ios mb-4">
              <p className="text-sm text-label-secondary">
                <strong>Workflow:</strong> Record the appliance MAC address → Register in Central Command →
                Ship appliance → Client plugs in → Appliance auto-fetches config
              </p>
            </div>

            <h5 className="font-semibold">Step 1: Get MAC Address</h5>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# From appliance boot screen or label on hardware
# Example: 84:3A:5B:91:B6:61

# Or via SSH if already booted:
ip link show | grep -A1 "state UP" | grep ether`}</pre>
            </div>

            <h5 className="font-semibold mt-4">Step 2: Register MAC in Central Command</h5>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Register MAC → Site mapping via API
curl -X POST https://api.osiriscare.net/api/provision \\
  -H "Content-Type: application/json" \\
  -d '{
    "mac_address": "84:3A:5B:91:B6:61",
    "site_id": "clinic-smith-abc123",
    "api_key": "q5VihYAYhKMH-vtX-DXuzLrjqbhgM61S5KjgPM4UG4A",
    "notes": "HP T640 for Smith Family Practice"
  }'

# Response:
{
  "status": "registered",
  "mac_address": "84:3A:5B:91:B6:61",
  "site_id": "clinic-smith-abc123",
  "message": "Appliance will auto-provision on first boot"
}`}</pre>
            </div>

            <h5 className="font-semibold mt-4">Step 3: Verify Registration</h5>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Test that MAC returns config (URL-encode the colons)
curl https://api.osiriscare.net/api/provision/84%3A3A%3A5B%3A91%3AB6%3A61

# Response:
{
  "site_id": "clinic-smith-abc123",
  "site_name": "Smith Family Practice",
  "api_endpoint": "https://api.osiriscare.net",
  "api_key": "q5VihYAYhKMH-vtX-DXuzLrjqbhgM61S5KjgPM4UG4A"
}`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Boot Sequence</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ol className="list-decimal list-inside space-y-2 text-label-secondary text-sm">
                <li><strong>Check for existing config</strong> - Skip if /var/lib/msp/config.yaml exists</li>
                <li><strong>Scan USB drives</strong> - Look for config.yaml in standard locations</li>
                <li><strong>MAC lookup</strong> - If no USB config, fetch from Central Command API</li>
                <li><strong>Write config</strong> - Save to /var/lib/msp/config.yaml</li>
                <li><strong>Start agent</strong> - Compliance agent begins phoning home</li>
              </ol>
            </div>

            <h4 className="font-semibold mt-6">API Reference</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3">Endpoint</th>
                    <th className="text-left py-2 px-3">Method</th>
                    <th className="text-left py-2 px-3">Description</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono text-xs">/api/provision/&lt;mac&gt;</td>
                    <td className="py-2 px-3">GET</td>
                    <td className="py-2 px-3">Get config for MAC address (used by appliance)</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono text-xs">/api/provision</td>
                    <td className="py-2 px-3">POST</td>
                    <td className="py-2 px-3">Register MAC → Site mapping</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-mono text-xs">/api/provision/&lt;mac&gt;</td>
                    <td className="py-2 px-3">DELETE</td>
                    <td className="py-2 px-3">Remove MAC registration</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="p-4 bg-health-warning/10 border-l-4 border-health-warning rounded-r-ios mt-4">
              <h4 className="font-semibold text-health-warning">Troubleshooting</h4>
              <ul className="mt-2 text-sm text-label-secondary space-y-1">
                <li><strong>No auto-provision:</strong> Check provision.log at /var/lib/msp/provision.log</li>
                <li><strong>USB not detected:</strong> Ensure FAT32/ext4 format, try different USB port</li>
                <li><strong>MAC not found:</strong> Verify MAC format (colons, uppercase), check registration</li>
                <li><strong>Network timeout:</strong> Ensure outbound HTTPS to api.osiriscare.net</li>
              </ul>
            </div>
          </div>
        ),
      },
      {
        id: 'partner-qr-provision-sop',
        title: 'SOP: Partner QR Code Provisioning',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-accent-primary/10 border-l-4 border-accent-primary rounded-r-ios">
              <p className="text-sm font-medium">Enable partners to onboard appliances using QR codes.</p>
              <p className="text-xs text-label-tertiary mt-1">New in Partner Infrastructure - Datto-style white-label distribution</p>
            </div>

            <h4 className="font-semibold">Partner Provisioning Flow</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ol className="list-decimal list-inside space-y-2 text-label-secondary text-sm">
                <li><strong>Partner logs into dashboard</strong> - /partner/login with API key</li>
                <li><strong>Partner creates provision code</strong> - Enters target client name</li>
                <li><strong>QR code generated</strong> - Contains claim URL with 16-char code</li>
                <li><strong>Partner ships appliance</strong> - Includes QR code printout or sticker</li>
                <li><strong>Client powers on appliance</strong> - Enters provisioning mode (no config)</li>
                <li><strong>Technician scans QR</strong> - Or manually enters 16-character code</li>
                <li><strong>Appliance calls /api/partners/claim</strong> - With code + MAC address</li>
                <li><strong>Server creates site</strong> - Under partner's umbrella</li>
                <li><strong>Appliance receives config</strong> - site_id, API key, partner branding</li>
                <li><strong>Normal operation begins</strong> - Phone-home every 60 seconds</li>
              </ol>
            </div>

            <h4 className="font-semibold mt-6">For Partners: Creating Provision Codes</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# 1. Log into Partner Dashboard
https://dashboard.osiriscare.net/partner/login

# Enter your API key (provided by OsirisCare)

# 2. Navigate to "Provision Codes" tab

# 3. Click "New Provision Code"
#    - Enter target client name (e.g., "Smith Family Practice")
#    - Code expires in 30 days by default

# 4. Click "QR" button to display the QR code
#    - Print or screenshot for technician
#    - Code displayed: XXXX-XXXX-XXXX-XXXX (16 chars)`}</pre>
            </div>

            <h4 className="font-semibold mt-6">For Partners: Via API</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Create provision code via API
curl -X POST https://api.osiriscare.net/api/partners/me/provisions \\
  -H "X-API-Key: YOUR_PARTNER_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "target_client_name": "Smith Family Practice",
    "expires_days": 30
  }'

# Response:
{
  "id": "abc123...",
  "provision_code": "ABCD1234EFGH5678",
  "qr_content": "https://api.osiriscare.net/api/partners/claim?code=ABCD1234EFGH5678",
  "status": "pending",
  "expires_at": "2026-02-04T00:00:00Z"
}`}</pre>
            </div>

            <h4 className="font-semibold mt-6">For Technicians: Provisioning an Appliance</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ol className="list-decimal list-inside space-y-2 text-label-secondary text-sm">
                <li>Power on the appliance (no prior config)</li>
                <li>Wait for NixOS to boot (2-3 minutes)</li>
                <li>Provisioning screen appears automatically</li>
                <li>Either:
                  <ul className="ml-6 mt-1 list-disc list-inside">
                    <li>Scan the QR code with a phone/tablet</li>
                    <li>Or enter the 16-character code manually</li>
                  </ul>
                </li>
                <li>Appliance shows "Provisioning..." then "Complete!"</li>
                <li>Agent starts automatically, begins checking in</li>
              </ol>
            </div>

            <h4 className="font-semibold mt-6">Appliance CLI Provisioning</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# If appliance boots without config.yaml, it enters provisioning mode:

============================================================
  OsirisCare Appliance Provisioning
============================================================

MAC Address: 84:3A:5B:91:B6:61
Hostname:    osiriscare-appliance

Enter your provision code (from partner dashboard):
Format: XXXXXXXXXXXXXXXX (16 characters)

Provision Code: ABCD1234EFGH5678

Provisioning...

============================================================
  Provisioning Complete!
============================================================

Site ID:     partner-clinic-abc123
Partner:     NEPA IT Solutions
Config:      /var/lib/msp/config.yaml

The agent will now restart in normal operation mode.`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Revenue Model</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold text-accent-primary">Default Split</h5>
                <p className="text-2xl font-bold mt-2">40% / 60%</p>
                <p className="text-xs text-label-tertiary mt-1">Partner gets 40%, OsirisCare 60%</p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold text-accent-primary">Custom Arrangements</h5>
                <p className="text-sm text-label-secondary mt-2">
                  Revenue share can be customized per partner (10-60% range).
                  Contact OsirisCare sales for volume pricing.
                </p>
              </div>
            </div>

            <h4 className="font-semibold mt-6">API Reference</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3">Endpoint</th>
                    <th className="text-left py-2 px-3">Method</th>
                    <th className="text-left py-2 px-3">Auth</th>
                    <th className="text-left py-2 px-3">Description</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono text-xs">/api/partners/me</td>
                    <td className="py-2 px-3">GET</td>
                    <td className="py-2 px-3">X-API-Key</td>
                    <td className="py-2 px-3">Get partner info and stats</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono text-xs">/api/partners/me/provisions</td>
                    <td className="py-2 px-3">GET</td>
                    <td className="py-2 px-3">X-API-Key</td>
                    <td className="py-2 px-3">List provision codes</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono text-xs">/api/partners/me/provisions</td>
                    <td className="py-2 px-3">POST</td>
                    <td className="py-2 px-3">X-API-Key</td>
                    <td className="py-2 px-3">Create provision code</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono text-xs">/api/partners/me/sites</td>
                    <td className="py-2 px-3">GET</td>
                    <td className="py-2 px-3">X-API-Key</td>
                    <td className="py-2 px-3">List partner's sites</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-mono text-xs">/api/partners/claim</td>
                    <td className="py-2 px-3">POST</td>
                    <td className="py-2 px-3">Public</td>
                    <td className="py-2 px-3">Claim code (appliance calls)</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="p-4 bg-health-warning/10 border-l-4 border-health-warning rounded-r-ios mt-4">
              <h4 className="font-semibold text-health-warning">Troubleshooting</h4>
              <ul className="mt-2 text-sm text-label-secondary space-y-1">
                <li><strong>Code not found:</strong> Verify code is 16 characters, case-insensitive</li>
                <li><strong>Code expired:</strong> Create a new provision code in partner dashboard</li>
                <li><strong>Code already claimed:</strong> Each code can only be used once</li>
                <li><strong>Network error:</strong> Ensure appliance has internet access to api.osiriscare.net</li>
              </ul>
            </div>
          </div>
        ),
      },
      {
        id: 'incident-response-sop',
        title: 'SOP: L3 Incident Response',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-health-critical/10 border-l-4 border-health-critical rounded-r-ios">
              <p className="text-sm font-medium">Handle incidents that auto-healing cannot resolve.</p>
              <p className="text-xs text-label-tertiary mt-1">Target response: 4 hours during business hours</p>
            </div>

            <h4 className="font-semibold">When L3 Escalation Occurs</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary text-sm">
              <li>L1 deterministic rules don't match</li>
              <li>L2 LLM planner cannot generate safe remediation</li>
              <li>Action requires human judgment or approval</li>
              <li>Client has custom exception or policy</li>
            </ul>

            <h4 className="font-semibold mt-6">Response Procedure</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3 w-12">#</th>
                    <th className="text-left py-2 px-3">Task</th>
                    <th className="text-left py-2 px-3">Time</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">1</td>
                    <td className="py-2 px-3 font-medium">Acknowledge incident in Central Command</td>
                    <td className="py-2 px-3">15 min</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">2</td>
                    <td className="py-2 px-3 font-medium">Review incident details and evidence</td>
                    <td className="py-2 px-3">15 min</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">3</td>
                    <td className="py-2 px-3 font-medium">Assess severity and impact</td>
                    <td className="py-2 px-3">10 min</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">4</td>
                    <td className="py-2 px-3 font-medium">Develop remediation plan</td>
                    <td className="py-2 px-3">30 min</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">5</td>
                    <td className="py-2 px-3 font-medium">Execute remediation (maintenance window if disruptive)</td>
                    <td className="py-2 px-3">Varies</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">6</td>
                    <td className="py-2 px-3 font-medium">Verify fix and document</td>
                    <td className="py-2 px-3">15 min</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-mono">7</td>
                    <td className="py-2 px-3 font-medium">Create L1 rule if pattern is repeatable</td>
                    <td className="py-2 px-3">30 min</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <h4 className="font-semibold mt-6">Severity Levels</h4>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="p-4 bg-health-critical/10 rounded-ios">
                <h5 className="font-semibold text-health-critical">Critical</h5>
                <p className="text-xs text-label-secondary mt-1">Active breach, data exposure, complete outage</p>
                <p className="text-xs text-label-tertiary mt-2">Response: Immediate</p>
              </div>
              <div className="p-4 bg-health-warning/10 rounded-ios">
                <h5 className="font-semibold text-health-warning">High</h5>
                <p className="text-xs text-label-secondary mt-1">Control failure, compliance gap, partial outage</p>
                <p className="text-xs text-label-tertiary mt-2">Response: 4 hours</p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold">Medium/Low</h5>
                <p className="text-xs text-label-secondary mt-1">Warning conditions, optimization opportunities</p>
                <p className="text-xs text-label-tertiary mt-2">Response: 24 hours</p>
              </div>
            </div>

            <h4 className="font-semibold mt-6">Promote to L1 Rule</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# After successful manual remediation, create L1 rule
# in mcp-server/runbooks/

# Example: RB-DISK-002.yaml
id: RB-DISK-002
name: Clear temp files when disk > 90%
trigger:
  control: storage_posture
  condition: disk_usage > 90
actions:
  - type: shell
    command: "find /tmp -mtime +7 -delete"
  - type: shell
    command: "journalctl --vacuum-time=7d"
outcome: success
evidence:
  - disk_usage_before
  - disk_usage_after`}</pre>
            </div>
          </div>
        ),
      },
      {
        id: 'backup-sop',
        title: 'SOP: Central Command Backup & Restore',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-accent-primary/10 border-l-4 border-accent-primary rounded-r-ios">
              <p className="text-sm font-medium">Automated encrypted backups to Hetzner Storage Box via Restic.</p>
              <p className="text-xs text-label-tertiary mt-1">Hourly backups • 24 hourly, 7 daily, 4 weekly, 6 monthly retention</p>
            </div>

            <h4 className="font-semibold">What's Backed Up</h4>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold text-accent-primary">PostgreSQL</h5>
                <p className="text-xs text-label-secondary mt-1">
                  Sites, appliances, incidents, evidence, portal tokens
                </p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold text-accent-primary">MinIO</h5>
                <p className="text-xs text-label-secondary mt-1">
                  Evidence bundles, compliance reports, audit logs
                </p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold text-accent-primary">Configs</h5>
                <p className="text-xs text-label-secondary mt-1">
                  Docker configs, environment files, SSL certs
                </p>
              </div>
            </div>

            <h4 className="font-semibold mt-6">Check Backup Status</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# API endpoint for dashboard
curl https://api.osiriscare.net/api/backup/status

# List available snapshots
curl https://api.osiriscare.net/api/backup/snapshots

# Manual status check on VPS
ssh root@178.156.162.116 "cat /opt/backups/status/latest.json"

# View systemd timer status
ssh root@178.156.162.116 "systemctl list-timers | grep osiris"`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Manual Backup</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Run backup manually (e.g., before major changes)
ssh root@178.156.162.116 "/opt/backups/scripts/backup.sh"

# Check backup size and stats
ssh root@178.156.162.116 "RESTIC_REPOSITORY='sftp:storagebox:/backups' \\
  RESTIC_PASSWORD_FILE=/root/.restic-password \\
  restic -o 'sftp.command=ssh storagebox -s sftp' stats"`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Disaster Recovery Procedure</h4>
            <div className="p-4 bg-health-critical/10 border-l-4 border-health-critical rounded-r-ios mb-4">
              <p className="text-sm font-medium">Only use this procedure for full disaster recovery.</p>
              <p className="text-xs text-label-tertiary mt-1">Will stop services and restore from backup snapshot</p>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3 w-12">#</th>
                    <th className="text-left py-2 px-3">Step</th>
                    <th className="text-left py-2 px-3">Command / Action</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">1</td>
                    <td className="py-2 px-3 font-medium">SSH to VPS</td>
                    <td className="py-2 px-3"><code className="text-xs">ssh root@178.156.162.116</code></td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">2</td>
                    <td className="py-2 px-3 font-medium">List snapshots</td>
                    <td className="py-2 px-3"><code className="text-xs">/opt/backups/scripts/restore.sh --list</code></td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">3</td>
                    <td className="py-2 px-3 font-medium">Run restore</td>
                    <td className="py-2 px-3"><code className="text-xs">/opt/backups/scripts/restore.sh --snapshot SNAPSHOT_ID</code></td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3 font-mono">4</td>
                    <td className="py-2 px-3 font-medium">Verify services</td>
                    <td className="py-2 px-3"><code className="text-xs">docker compose ps && curl localhost:8000/health</code></td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3 font-mono">5</td>
                    <td className="py-2 px-3 font-medium">Test dashboard</td>
                    <td className="py-2 px-3">Login to Central Command, verify data</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <h4 className="font-semibold mt-6">Backup Credentials (Secure Storage)</h4>
            <div className="p-4 bg-health-warning/10 border-l-4 border-health-warning rounded-r-ios">
              <p className="text-sm text-label-secondary">
                <strong>Storage Box:</strong> u526501.your-storagebox.de:23<br />
                <strong>Restic Password File:</strong> /root/.restic-password<br />
                <strong>SSH Key:</strong> /root/.ssh/storagebox_backup<br />
                <strong>Retention:</strong> 24 hourly, 7 daily, 4 weekly, 6 monthly<br />
                <strong>Location:</strong> Hetzner FSN1 (Falkenstein, Germany)
              </p>
            </div>

            <h4 className="font-semibold mt-6">Hetzner Cloud Snapshots</h4>
            <div className="p-4 bg-accent-primary/10 border-l-4 border-accent-primary rounded-r-ios mb-4">
              <p className="text-sm font-medium">Full server disk images for bare-metal recovery.</p>
              <p className="text-xs text-label-tertiary mt-1">Weekly snapshots complement Restic backups for complete disaster recovery.</p>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              <div className="p-3 bg-fill-secondary rounded-ios text-center">
                <div className="text-accent-primary font-bold">Weekly</div>
                <div className="text-xs text-label-tertiary">Frequency</div>
              </div>
              <div className="p-3 bg-fill-secondary rounded-ios text-center">
                <div className="text-accent-primary font-bold">4</div>
                <div className="text-xs text-label-tertiary">Retention</div>
              </div>
              <div className="p-3 bg-fill-secondary rounded-ios text-center">
                <div className="text-accent-primary font-bold">~75 GB</div>
                <div className="text-xs text-label-tertiary">Per Snapshot</div>
              </div>
              <div className="p-3 bg-fill-secondary rounded-ios text-center">
                <div className="text-accent-primary font-bold">~$4/mo</div>
                <div className="text-xs text-label-tertiary">Est. Cost</div>
              </div>
            </div>

            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Check snapshot status
curl https://api.osiriscare.net/api/snapshot/status

# Manual snapshot
ssh root@178.156.162.116 "/opt/backups/scripts/hetzner-snapshot.sh"

# List snapshots via hcloud
ssh root@178.156.162.116 "export HCLOUD_TOKEN=\\$(cat /root/.hcloud-token) && hcloud image list --type snapshot"

# Restore from snapshot (Hetzner Console)
# Servers → mcp-osiriscare-net → Rebuild → Select snapshot`}</pre>
            </div>

            <div className="p-4 bg-fill-secondary rounded-ios mt-4">
              <p className="text-sm text-label-secondary">
                <strong>Schedule:</strong> Sundays 04:00 UTC (after Restic integrity check)<br />
                <strong>Token:</strong> /root/.hcloud-token (600 permissions)<br />
                <strong>Script:</strong> /opt/backups/scripts/hetzner-snapshot.sh
              </p>
            </div>
          </div>
        ),
      },
      {
        id: 'disaster-recovery-sop',
        title: 'SOP: Disaster Recovery Runbook',
        content: (
          <div className="space-y-4">
            <div className="p-4 bg-health-critical/10 border-l-4 border-health-critical rounded-r-ios">
              <p className="text-sm font-medium">Complete VPS recovery procedure for Central Command.</p>
              <p className="text-xs text-label-tertiary mt-1">Target RTO: 30 minutes | RPO: 1 hour (Restic) or 1 week (Snapshot)</p>
            </div>

            <h4 className="font-semibold">Phase 1: Assess (2 min)</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ul className="space-y-2 text-sm text-label-secondary">
                <li className="flex items-start gap-2">
                  <span className="text-accent-primary">1.</span>
                  <span>Check if VPS is responding: <code className="bg-gray-900 px-1 rounded">ssh root@178.156.162.116</code></span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-accent-primary">2.</span>
                  <span>Check Hetzner status: <a href="https://status.hetzner.com" className="text-accent-primary underline">status.hetzner.com</a></span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-accent-primary">3.</span>
                  <span>Determine: Network issue? Server crash? Data corruption?</span>
                </li>
              </ul>
            </div>

            <h4 className="font-semibold mt-6">Phase 2: Decide Recovery Path</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold text-accent-primary">Option A: Restic Restore</h5>
                <p className="text-xs text-label-secondary mt-1">Server alive, data corrupted</p>
                <p className="text-xs text-label-tertiary mt-2">RPO: ~1 hour | Time: 10-15 min</p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <h5 className="font-semibold text-accent-primary">Option B: Snapshot Rebuild</h5>
                <p className="text-xs text-label-secondary mt-1">Server dead, need new instance</p>
                <p className="text-xs text-label-tertiary mt-2">RPO: ~1 week | Time: 15-20 min</p>
              </div>
            </div>

            <h4 className="font-semibold mt-6">Option A: Restic Restore (Server Alive)</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# SSH to the server
ssh root@178.156.162.116

# List available snapshots
/opt/backups/scripts/restore.sh --list

# Run interactive restore
/opt/backups/scripts/restore.sh

# Or specify snapshot ID directly
/opt/backups/scripts/restore.sh latest

# Verify services
docker compose ps
curl http://localhost:8000/health`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Option B: Hetzner Snapshot Rebuild (Server Dead)</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Step 1: Create new server from snapshot
# Hetzner Console → Servers → Create Server
# - Location: ash-dc1 (or any available)
# - Type: CPX31 (or better)
# - Image: Snapshots → osiriscare-weekly-YYYY-MM-DD
# - SSH Key: Select your key
# - Create

# Note the NEW IP address
NEW_IP="xxx.xxx.xxx.xxx"

# Step 2: SSH to new server
ssh root@$NEW_IP

# Step 3: Restore latest database from Restic
/opt/backups/scripts/restore.sh latest

# Step 4: Verify services
docker compose ps
curl http://localhost:8000/health`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Phase 3: Update DNS (if IP changed)</h4>
            <div className="p-4 bg-gray-900 rounded-ios text-green-400 font-mono text-sm overflow-x-auto">
              <pre>{`# Cloudflare Dashboard (recommended)
# 1. Login to Cloudflare
# 2. Select osiriscare.net
# 3. DNS → Update A records:
#    - api.osiriscare.net → NEW_IP
#    - dashboard.osiriscare.net → NEW_IP

# Wait for propagation (usually 1-5 min with Cloudflare)

# Verify DNS
dig api.osiriscare.net +short`}</pre>
            </div>

            <h4 className="font-semibold mt-6">Phase 4: Verify & Monitor</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <ul className="space-y-2 text-sm text-label-secondary">
                <li className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span>API responding: <code className="bg-gray-900 px-1 rounded">curl https://api.osiriscare.net/health</code></span>
                </li>
                <li className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span>Dashboard accessible: <code className="bg-gray-900 px-1 rounded">https://dashboard.osiriscare.net</code></span>
                </li>
                <li className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span>Appliances checking in: <code className="bg-gray-900 px-1 rounded">docker compose logs -f mcp-server | grep checkin</code></span>
                </li>
                <li className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span>Portal links working (test magic link login)</span>
                </li>
                <li className="flex items-center gap-2">
                  <input type="checkbox" className="rounded" />
                  <span>Backup timers running: <code className="bg-gray-900 px-1 rounded">systemctl list-timers | grep osiris</code></span>
                </li>
              </ul>
            </div>

            <h4 className="font-semibold mt-6">Credentials Reference</h4>
            <div className="p-4 bg-health-warning/10 border-l-4 border-health-warning rounded-r-ios">
              <p className="text-sm text-label-secondary">
                <strong>VPS:</strong> root@178.156.162.116 (SSH key auth)<br />
                <strong>Storage Box:</strong> u526501.your-storagebox.de:23<br />
                <strong>Restic Password:</strong> /root/.restic-password<br />
                <strong>Hetzner Token:</strong> /root/.hcloud-token<br />
                <strong>Hetzner Console:</strong> console.hetzner.cloud (credentials in password manager)<br />
                <strong>Cloudflare:</strong> dash.cloudflare.com (credentials in password manager)
              </p>
            </div>

            <h4 className="font-semibold mt-6">Backup Schedule Summary</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light">
                    <th className="text-left py-2 px-3">Time (UTC)</th>
                    <th className="text-left py-2 px-3">Day</th>
                    <th className="text-left py-2 px-3">Task</th>
                    <th className="text-left py-2 px-3">RPO</th>
                  </tr>
                </thead>
                <tbody className="text-label-secondary">
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3">XX:01</td>
                    <td className="py-2 px-3">Hourly</td>
                    <td className="py-2 px-3">Restic backup to Storage Box</td>
                    <td className="py-2 px-3 text-health-good">1 hour</td>
                  </tr>
                  <tr className="border-b border-separator-light/50">
                    <td className="py-2 px-3">03:00</td>
                    <td className="py-2 px-3">Sunday</td>
                    <td className="py-2 px-3">Restic integrity check</td>
                    <td className="py-2 px-3">-</td>
                  </tr>
                  <tr>
                    <td className="py-2 px-3">04:00</td>
                    <td className="py-2 px-3">Sunday</td>
                    <td className="py-2 px-3">Hetzner Cloud snapshot</td>
                    <td className="py-2 px-3 text-health-warning">1 week</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="p-4 bg-fill-secondary rounded-ios mt-4">
              <h5 className="font-semibold text-sm">Total Backup Cost</h5>
              <p className="text-sm text-label-secondary mt-1">
                Storage Box (1TB): ~$4/mo + Hetzner Snapshots (~300GB): ~$4/mo = <strong>~$8.50/mo</strong>
              </p>
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
      {
        id: 'client-access-sop',
        title: 'SOP: Client Portal Access Guide',
        content: (
          <div className="space-y-6">
            <div className="p-4 bg-accent-primary/10 border border-accent-primary rounded-ios">
              <p className="text-sm">
                <strong>Purpose:</strong> This SOP provides step-by-step instructions for administrators
                to help clients access their HIPAA compliance portal. Use this guide when onboarding
                new clients or when clients need assistance.
              </p>
            </div>

            <h4 className="font-semibold text-lg">Step 1: Generate Portal Link</h4>
            <div className="p-4 bg-fill-secondary rounded-ios space-y-3">
              <ol className="list-decimal list-inside space-y-2 text-label-secondary">
                <li>Log in to Central Command (msp.osiriscare.net)</li>
                <li>Navigate to <strong>Sites</strong> in the sidebar</li>
                <li>Click on the client's site name</li>
                <li>Click the <strong>"Generate Portal Link"</strong> button</li>
                <li>Copy the generated URL</li>
              </ol>
              <p className="text-xs text-label-tertiary mt-2">
                Note: Links do not expire by default. Generate a new link if you suspect the old one was compromised.
              </p>
            </div>

            <h4 className="font-semibold text-lg">Step 2: Send to Client</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <p className="text-sm text-label-secondary mb-3">
                Use the email template below. Copy and customize with client details:
              </p>
              <div className="p-4 bg-gray-900 rounded-ios text-gray-300 text-sm font-mono whitespace-pre-wrap">
{`Subject: Your HIPAA Compliance Portal Access - [Practice Name]

Hi [Contact Name],

Your HIPAA compliance monitoring portal is now active. You can access your real-time compliance dashboard at any time using this secure link:

[PASTE PORTAL LINK HERE]

What You'll See:
• Overall compliance health score
• Status of all 8 HIPAA security controls
• Recent automated remediation activity
• Evidence bundle archive for audit purposes

No login required - simply click the link to view your dashboard.

Tips:
• Bookmark this link for easy access
• Share with your compliance officer or practice manager
• The dashboard updates in real-time as our appliance monitors your systems

Questions? Reply to this email or call us at [Support Number].

Best regards,
[Your Name]
OsirisCare Compliance Team`}
              </div>
            </div>

            <h4 className="font-semibold text-lg">Step 3: Walk Client Through Portal</h4>
            <div className="p-4 bg-fill-secondary rounded-ios">
              <p className="text-sm text-label-secondary mb-3">
                During initial setup, offer a brief walkthrough. Key sections to explain:
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-3">
                <div className="p-3 bg-background-primary rounded-ios">
                  <h5 className="font-medium text-accent-primary">Health Score</h5>
                  <p className="text-xs text-label-tertiary mt-1">
                    Overall percentage showing compliance status. Green (90%+) = healthy,
                    Yellow (70-89%) = attention needed, Red (&lt;70%) = critical issues.
                  </p>
                </div>
                <div className="p-3 bg-background-primary rounded-ios">
                  <h5 className="font-medium text-accent-primary">Control Tiles</h5>
                  <p className="text-xs text-label-tertiary mt-1">
                    8 tiles showing each HIPAA control status. Click any tile to see
                    details and which HIPAA sections it addresses (e.g., 164.308, 164.312).
                  </p>
                </div>
                <div className="p-3 bg-background-primary rounded-ios">
                  <h5 className="font-medium text-accent-primary">Auto-Fix Log</h5>
                  <p className="text-xs text-label-tertiary mt-1">
                    Shows recent automated remediations. Clients can see what was fixed
                    without needing to take action - demonstrates system value.
                  </p>
                </div>
                <div className="p-3 bg-background-primary rounded-ios">
                  <h5 className="font-medium text-accent-primary">Evidence Bundles</h5>
                  <p className="text-xs text-label-tertiary mt-1">
                    Cryptographically signed compliance checks. These serve as audit
                    evidence for HIPAA compliance reviews.
                  </p>
                </div>
              </div>
            </div>

            <h4 className="font-semibold text-lg">Common Client Questions</h4>
            <div className="space-y-3">
              <div className="p-4 bg-fill-secondary rounded-ios">
                <p className="font-medium">"Do I need to log in?"</p>
                <p className="text-sm text-label-tertiary mt-1">
                  No. The portal uses a secure magic link - just click the URL we sent.
                  No username or password needed. The link itself contains your secure access token.
                </p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <p className="font-medium">"Who can I share this link with?"</p>
                <p className="text-sm text-label-tertiary mt-1">
                  Share only with authorized personnel at your practice - compliance officers,
                  practice managers, or administrators. Anyone with the link can view your
                  compliance data. Contact us if you need the link revoked and regenerated.
                </p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <p className="font-medium">"How often does this update?"</p>
                <p className="text-sm text-label-tertiary mt-1">
                  Real-time. The appliance at your site performs checks every 60 seconds
                  and reports status continuously. What you see on the portal is current
                  as of the page load.
                </p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <p className="font-medium">"What do I do if something is red/failing?"</p>
                <p className="text-sm text-label-tertiary mt-1">
                  Nothing, usually! Our system automatically remediates most issues within
                  seconds. If a control stays red, we're already alerted and working on it.
                  You'll see it turn green once resolved.
                </p>
              </div>
              <div className="p-4 bg-fill-secondary rounded-ios">
                <p className="font-medium">"Can I download reports for auditors?"</p>
                <p className="text-sm text-label-tertiary mt-1">
                  Yes. Click "Download Monthly Report" to get a PDF compliance packet.
                  For specific evidence bundles, use the "Evidence" section to download
                  signed proof of compliance checks.
                </p>
              </div>
            </div>

            <h4 className="font-semibold text-lg">Troubleshooting Client Access</h4>
            <div className="p-4 bg-health-warning/10 border-l-4 border-health-warning rounded-r-ios">
              <h5 className="font-medium">If client reports "Page Not Found" or "Invalid Token":</h5>
              <ol className="mt-2 list-decimal list-inside text-sm text-label-secondary space-y-1">
                <li>Verify the URL wasn't truncated in email (check for "..." in link)</li>
                <li>Generate a fresh portal link from Central Command</li>
                <li>Send the new link directly (avoid copy-paste through multiple apps)</li>
                <li>If issue persists, check site status in Central Command</li>
              </ol>
            </div>
          </div>
        ),
      },
      {
        id: 'portal-security',
        title: 'Portal Security & Link Management',
        content: (
          <div className="space-y-4">
            <p className="text-label-secondary">
              Portal links use secure tokens that provide read-only access. Understanding
              security implications helps you advise clients appropriately.
            </p>

            <h4 className="font-semibold">Token Security Model</h4>
            <ul className="list-disc list-inside space-y-2 text-label-secondary">
              <li><strong>Read-only access:</strong> Clients cannot modify settings or data</li>
              <li><strong>Site-scoped:</strong> Each token only works for one specific site</li>
              <li><strong>No expiration by default:</strong> Links remain valid until regenerated</li>
              <li><strong>No authentication required:</strong> Anyone with the link can access</li>
            </ul>

            <h4 className="font-semibold mt-4">When to Regenerate Links</h4>
            <div className="p-4 bg-health-critical/10 border-l-4 border-health-critical rounded-r-ios">
              <ul className="space-y-2 text-sm text-label-secondary">
                <li>• Employee with portal access leaves the practice</li>
                <li>• Link was accidentally shared publicly</li>
                <li>• Client suspects unauthorized access</li>
                <li>• As part of regular security rotation (recommended quarterly)</li>
              </ul>
            </div>

            <h4 className="font-semibold mt-4">Regenerating a Link</h4>
            <ol className="list-decimal list-inside space-y-2 text-label-secondary">
              <li>Go to Sites → [Client Name] in Central Command</li>
              <li>Click "Generate Portal Link" (this invalidates the old token)</li>
              <li>Send the new link to authorized client contacts</li>
              <li>Confirm with client that old bookmarks should be updated</li>
            </ol>

            <h4 className="font-semibold mt-4">Data Visible on Portal</h4>
            <p className="text-sm text-label-secondary mb-2">
              Clients can view (but not download raw data for):
            </p>
            <ul className="list-disc list-inside space-y-1 text-label-secondary text-sm">
              <li>Compliance scores and health metrics</li>
              <li>Control pass/fail status with HIPAA mappings</li>
              <li>Incident history and auto-fix logs</li>
              <li>Evidence bundle summaries (downloadable as signed bundles)</li>
              <li>Monthly compliance reports (downloadable as PDF)</li>
            </ul>

            <div className="p-4 bg-accent-primary/10 rounded-ios mt-4">
              <p className="text-sm">
                <strong>Note:</strong> No PHI or sensitive system credentials are ever
                exposed through the client portal. All evidence is scrubbed of personally
                identifiable information before display.
              </p>
            </div>
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

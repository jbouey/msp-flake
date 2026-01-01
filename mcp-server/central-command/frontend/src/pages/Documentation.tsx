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

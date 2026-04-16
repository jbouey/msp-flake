import React from 'react';
import { ComparePage, type CompareRow } from '../components/marketing/ComparePage';

const ROWS: CompareRow[] = [
  {
    dimension: 'Primary market',
    osiris: 'Healthcare — single clinics through multi-site provider networks and DSOs. Purpose-built HIPAA.',
    competitor: 'Horizontal GRC across industries. SOC 2 is the flagship; HIPAA is supported as one of many frameworks.',
    winner: 'tie',
  },
  {
    dimension: 'On-site monitoring',
    osiris: 'Physical appliance on the LAN. Discovers every L2/L3 device, even on other VLANs. PHI scrubbed at egress.',
    competitor: 'Cloud collectors plus optional lightweight host agents. No LAN-level device discovery.',
    winner: 'osiris',
  },
  {
    dimension: 'Evidence model',
    osiris: 'Ed25519-signed, hash-chained, OpenTimestamps-anchored bundles. Auditor verifies independently on their laptop with a shell script.',
    competitor: 'Screenshots, policy documents, collected control attestations. Trust model is trust-the-vendor.',
    winner: 'osiris',
  },
  {
    dimension: 'Framework coverage',
    osiris: '9 frameworks (HIPAA, SOC 2, PCI DSS, NIST CSF, CIS, SOX, GDPR, CMMC, ISO 27001) with single evidence-bundle crosswalk.',
    competitor: '20+ frameworks covered as separate audit programs.',
    winner: 'competitor',
  },
  {
    dimension: 'Pricing model',
    osiris: 'Fleet pricing. $499 floor per practice. No per-seat metering.',
    competitor: 'Per-person and per-framework metering. Entry-level starts in the $10k+/year range.',
    winner: 'osiris',
  },
  {
    dimension: 'Multi-site support',
    osiris: 'Backend-authoritative mesh, cross-subnet device discovery, hash-ring target assignment, deterministic failover when an appliance goes offline.',
    competitor: 'Cloud-collector model scales with seats, not with physical sites.',
    winner: 'osiris',
  },
  {
    dimension: 'Remediation automation',
    osiris: 'L1 deterministic runbooks (70–80%, <100ms), L2 LLM-planned (15–20%), L3 human escalation. Every remediation writes a signed attestation.',
    competitor: 'Workflow automation and integrations; remediation is typically human-performed against flagged findings.',
    winner: 'osiris',
  },
  {
    dimension: 'Audit-room experience',
    osiris: 'Auditor downloads ZIP verifier kit. Runs verify.sh on their own laptop. No vendor dependency on the verification path.',
    competitor: 'Auditor receives exports, reports, and screenshots. Verification relies on vendor-authored artifacts.',
    winner: 'osiris',
  },
  {
    dimension: 'Onboarding speed',
    osiris: 'Appliance ships to site, plugs in, auto-provisions on first checkin. Typically online in 30 minutes.',
    competitor: 'Integration-heavy onboarding. 30–90 day implementation projects are common.',
    winner: 'osiris',
  },
  {
    dimension: 'Partner channel',
    osiris: 'Flat 20% partner margin. Multi-tenant partner portal with client RLS isolation. White-labelable client portal at Professional+.',
    competitor: 'Reseller program with negotiated tiers. Partner experience optimized for mid-market and up.',
    winner: 'osiris',
  },
];

export const CompareVanta: React.FC = () => (
  <ComparePage
    competitorName="Vanta"
    canonicalSlug="vanta"
    tagline="A horizontal GRC platform for companies pursuing SOC 2, vs a healthcare-purpose-built compliance substrate."
    narrativeIntro="Vanta is a strong choice for technology companies whose primary compliance driver is SOC 2 or ISO 27001. OsirisCare is a better choice for healthcare organizations whose primary compliance driver is HIPAA — especially single clinics, multi-location groups, and DSOs that need an on-site appliance to monitor endpoints, networks, and the physical environment that cloud-only collectors cannot see."
    theirStrengths={[
      'Broadest framework library in the GRC market (20+ frameworks supported).',
      'Strong ecosystem of integrations with HR, identity, cloud providers.',
      'Polished audit-partner marketplace for customers pursuing first-time SOC 2.',
      'Well-funded; product development velocity is high.',
    ]}
    rows={ROWS}
    whoShouldPickUs="Your primary regulatory driver is HIPAA, your organization serves clinical or multi-clinical healthcare, you need on-site appliance monitoring for endpoints and network devices, or you need evidence that your auditor can cryptographically verify without trusting any vendor."
    whoShouldPickThem="Your primary regulatory driver is SOC 2 or ISO 27001, your team is cloud-native with no on-premise footprint, or you need a broader framework library than OsirisCare's 9 currently supported."
  />
);

export default CompareVanta;

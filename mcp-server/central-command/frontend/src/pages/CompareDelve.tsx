import React from 'react';
import { ComparePage, type CompareRow } from '../components/marketing/ComparePage';

/**
 * CompareDelve — /compare/delve
 *
 * Positioned honestly around evidence verifiability rather than named
 * incident-driven smear. The April 2026 market event involving a
 * compliance automation vendor and fabricated audit reports makes
 * "can an auditor independently verify this?" the central buying
 * question for prospects evaluating both platforms.
 */
const ROWS: CompareRow[] = [
  {
    dimension: 'Evidence chain',
    osiris: 'Every bundle Ed25519-signed, hash-chained, OpenTimestamps-anchored. Auditor runs verify.sh on their own laptop — no vendor API calls required.',
    competitor: 'Platform-generated reports and dashboards. Verification path runs through the vendor platform.',
    winner: 'osiris',
  },
  {
    dimension: 'Independent verifiability',
    osiris: 'Open-source verifier kit shipped as a ZIP: README, verify.sh, chain.json, bundles, public keys, OpenTimestamps proofs. Verification continues to work if OsirisCare disappears tomorrow.',
    competitor: 'Verification depends on continued platform availability and platform-signed reports.',
    winner: 'osiris',
  },
  {
    dimension: 'Public security advisory practice',
    osiris: 'Public security advisories issued for every integrity-relevant incident. Past advisories inline-embedded in every auditor kit chain.json.',
    competitor: 'Security disclosure practice depends on the vendor — varies by incident.',
    winner: 'osiris',
  },
  {
    dimension: 'On-site monitoring',
    osiris: 'Physical appliance with cross-subnet device discovery and PHI scrubbing at egress.',
    competitor: 'Generally cloud-collector or SaaS-only.',
    winner: 'osiris',
  },
  {
    dimension: 'Multi-site coordination',
    osiris: 'Backend-authoritative mesh with deterministic hash-ring target assignment. Multi-appliance per site. Fleet-wide dashboards.',
    competitor: 'Account model scales with subscriptions, not with physical sites.',
    winner: 'osiris',
  },
  {
    dimension: 'Framework coverage',
    osiris: '9 frameworks (HIPAA, SOC 2, PCI DSS, NIST CSF, CIS, SOX, GDPR, CMMC, ISO 27001).',
    competitor: 'Varies; several frameworks typically offered.',
    winner: 'tie',
  },
  {
    dimension: 'Portability on exit',
    osiris: 'All evidence bundles remain verifiable forever after account closure. No vendor lock on the audit chain.',
    competitor: 'Evidence portability depends on vendor terms and export tooling.',
    winner: 'osiris',
  },
  {
    dimension: 'Pricing transparency',
    osiris: 'Published $499 floor, published fleet tiers, published 20% partner margin.',
    competitor: 'Custom-quote is common in this segment.',
    winner: 'osiris',
  },
];

export const CompareDelve: React.FC = () => (
  <ComparePage
    competitorName="Delve"
    canonicalSlug="delve"
    tagline="Evidence your auditor verifies themselves, vs reports your auditor verifies through the vendor."
    narrativeIntro="If you are here because a recent event in the compliance-automation market has left you wondering whether your audit evidence is worth what was paid for it, the comparison reduces to a single question: can an independent auditor verify the evidence without trusting the platform that produced it? OsirisCare was designed so the answer is always yes. Every evidence bundle is Ed25519-signed per appliance, hash-chained, and OpenTimestamps-anchored to a public blockchain — the auditor downloads a ZIP, runs a shell script, and confirms the chain on their own laptop."
    theirStrengths={[
      'Rapid onboarding flow for first-time compliance customers.',
      'Modern user interface for compliance dashboards.',
      'Marketing motion optimized for early-stage companies.',
    ]}
    rows={ROWS}
    whoShouldPickUs="You want evidence that survives the compliance-automation vendor itself. You want cryptographic verifiability as the default, not a premium feature. You operate in healthcare and need on-site monitoring of clinical endpoints. You want a public changelog, public security advisories, and no trust-the-vendor leap in your audit chain."
    whoShouldPickThem="You value the specific user-interface style and onboarding flow the platform provides, and verifiable evidence chains are not currently a priority for your audit program."
  />
);

export default CompareDelve;

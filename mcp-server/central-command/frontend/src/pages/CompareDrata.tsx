import React from 'react';
import { ComparePage, type CompareRow } from '../components/marketing/ComparePage';

const ROWS: CompareRow[] = [
  {
    dimension: 'Primary market',
    osiris: 'Healthcare-only. Single clinics, multi-location groups, DSOs, health-system IT.',
    competitor: 'Horizontal compliance automation. Strong mid-market presence across SaaS, fintech, healthcare.',
    winner: 'tie',
  },
  {
    dimension: 'On-site monitoring',
    osiris: 'Physical appliance on the LAN with cross-subnet device discovery and PHI scrubbing at egress.',
    competitor: 'SaaS-only. Cloud integrations plus optional endpoint agents. No on-site device discovery.',
    winner: 'osiris',
  },
  {
    dimension: 'Evidence verifiability',
    osiris: 'Ed25519-signed, hash-chained, Bitcoin-anchored bundles. Auditor verifies with verify.sh, no vendor API calls required.',
    competitor: 'Platform-generated reports and evidence collection. Verification runs through the Drata audit hub.',
    winner: 'osiris',
  },
  {
    dimension: 'Automation depth',
    osiris: 'Three-tier self-healing: L1 deterministic (<100ms, 70–80%), L2 LLM-planned (15–20%), L3 human (5–10%). Remediation is signed and attested, not just logged.',
    competitor: 'Strong automation for evidence collection. Remediation of flagged findings remains operator-driven via workflow integrations.',
    winner: 'osiris',
  },
  {
    dimension: 'Multi-site / multi-appliance',
    osiris: 'Backend-authoritative mesh. Server-side hash-ring target assignment. Deterministic failover when an appliance goes offline. Each site can run multiple appliances for redundancy.',
    competitor: 'Account scales with seats and connectors, not with physical locations.',
    winner: 'osiris',
  },
  {
    dimension: 'Framework breadth',
    osiris: '9 frameworks today with single-evidence crosswalk. Roadmap driven by healthcare demand.',
    competitor: '15+ frameworks supported, broader reach outside healthcare.',
    winner: 'competitor',
  },
  {
    dimension: 'Pricing transparency',
    osiris: '$499 floor published. Fleet tiers published. Partner margin published (20% flat).',
    competitor: 'Custom-quote model. Published pricing is unusual in this segment.',
    winner: 'osiris',
  },
  {
    dimension: 'Time to first signed evidence',
    osiris: 'First Ed25519-signed evidence bundle is produced within minutes of appliance first-checkin.',
    competitor: 'First platform report is produced after integrations are wired and initial policies are mapped — typically 1–4 weeks.',
    winner: 'osiris',
  },
  {
    dimension: 'Partner / MSP channel',
    osiris: 'Multi-tenant partner portal with per-client RLS enforcement. Flat 20% margin. Healthcare-focused partner list.',
    competitor: 'Partner program oriented around mid-market and enterprise reseller motions.',
    winner: 'osiris',
  },
  {
    dimension: 'Exit / portability',
    osiris: 'Every evidence bundle remains verifiable after an account closes. No vendor lock on the cryptographic chain.',
    competitor: 'Evidence and reports live in the platform; export is available but tied to the Drata environment.',
    winner: 'osiris',
  },
];

export const CompareDrata: React.FC = () => (
  <ComparePage
    competitorName="Drata"
    canonicalSlug="drata"
    tagline="A horizontal SaaS compliance automation platform, vs a healthcare-purpose-built substrate with on-site hardware."
    narrativeIntro="Drata has earned its reputation in the mid-market compliance-automation space with strong SOC 2 and ISO 27001 motion and a polished auditor-handoff workflow. OsirisCare is a different product for a different primary customer: healthcare organizations whose clinical environment cannot be fully monitored by cloud integrations and whose auditor increasingly wants to verify evidence without trusting the compliance vendor."
    theirStrengths={[
      'Strong mid-market SOC 2 / ISO 27001 audit-readiness motion.',
      'Mature integration library for cloud-native infrastructure (AWS, GCP, Azure, Okta, etc.).',
      'Polished auditor hub with PBC-list management.',
      'Large customer base; partner ecosystem well established outside healthcare.',
    ]}
    rows={ROWS}
    whoShouldPickUs="Your primary regulatory driver is HIPAA, you need on-site monitoring of clinical endpoints and network devices, you want evidence the auditor can verify independently, or you need multi-site / multi-appliance coordination under one fleet view."
    whoShouldPickThem="Your primary regulatory driver is SOC 2 or ISO 27001 with HIPAA secondary, your environment is entirely cloud-based with no on-premise hardware to monitor, or you need a framework library larger than OsirisCare's 9."
  />
);

export default CompareDrata;

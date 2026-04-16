import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { MarketingLayout } from '../components/marketing/MarketingLayout';
import { JsonLd } from '../components/marketing/JsonLd';

/**
 * Hipaa2026Update — /2026-hipaa-update
 *
 * The NPRM-focused landing page. Positioning scope deliberately covers
 * single clinics AND multi-site provider networks — OsirisCare's
 * backend-authoritative mesh, cross-subnet appliance coordination,
 * and 9-framework compliance engine are not "small practice" features;
 * they are what lets a 20-location DSO meet the same rule with the
 * same platform.
 *
 * Each of the nine NPRM requirements is mapped to a concrete OsirisCare
 * capability so practice managers, DSO IT directors, and partner MSPs
 * can all see themselves in the mapping.
 */

const NPRM_CHANGES: Array<{
  id: string;
  title: string;
  citation: string;
  change: string;
  painForHealthcare: string;
  osirisCapability: string;
}> = [
  {
    id: 'mfa',
    title: 'Multi-factor authentication — required (not addressable)',
    citation: '§ 164.312(a)(2)(iii) proposed',
    change:
      'MFA becomes a required technical safeguard for all ePHI access, including remote workforce and any workforce with privileged access.',
    painForHealthcare:
      'A solo clinic with password-only VPN access and a 20-location DSO with inconsistent MFA enforcement across regional EMRs both fail the same check. Self-attestation is no longer enough — continuous verification is now the rule.',
    osirisCapability:
      'mfa_enabled check runs continuously against every ePHI-touching workstation and identity provider, per-site and per-appliance. Multi-site deployments get a fleet-wide dashboard showing every host missing MFA, with Ed25519-signed evidence captured on each check cycle.',
  },
  {
    id: 'encryption',
    title: 'Encryption at rest and in transit — required',
    citation: '§ 164.312(a)(2)(iv) + (e)(2)(ii) proposed',
    change:
      'ePHI at rest and in transit must be encrypted. The "addressable with documented equivalent measure" escape hatch is removed.',
    painForHealthcare:
      'Unencrypted USB drives, unencrypted backups to NAS, HTTP-only intranet EMR portals, SMBv1 file shares, and lateral-movement-friendly flat networks all become explicit violations across every site.',
    osirisCapability:
      'bitlocker_enabled, filevault_enabled, luks_enabled, tls_version, smb_signing checks run per-host. For multi-appliance sites the mesh ring deterministically distributes checks across appliances so coverage is complete even when one appliance is offline.',
  },
  {
    id: 'network-segmentation',
    title: 'Network segmentation — documented and enforced',
    citation: '§ 164.312(d)(3) proposed',
    change:
      'Regulated entities must implement and document technical controls separating ePHI systems from systems that do not need access.',
    painForHealthcare:
      'Most practices — single-clinic and multi-site alike — run flat LANs. Every waiting-room tablet, IoT thermostat, and staff BYOD phone on the same subnet as the EMR server is a finding. At DSO scale this compounds fast.',
    osirisCapability:
      'Cross-subnet device discovery (not limited to the appliance\'s own subnet) maps every L2/L3 device the appliance can see. Per-site segmentation reports identify devices that sit alongside ePHI systems without a business need, with signed evidence for each discovery cycle.',
  },
  {
    id: 'asset-inventory',
    title: 'Asset inventory + network map — annual refresh required',
    citation: '§ 164.308(a)(1)(ii)(D) proposed',
    change:
      'A written inventory of technology assets and a written network map must be maintained and reviewed at least annually.',
    painForHealthcare:
      'Most practices have no inventory. Multi-site organizations have partial inventories per site, none of them reconciled. When an auditor asks "what printer is in exam room 4 at the Scranton location?" silence is the finding.',
    osirisCapability:
      'Every discovered device across every site is cataloged continuously. The partner or client portal exports a fleet-wide, timestamped, Ed25519-signed inventory on demand — satisfying the annual-review requirement with audit-grade evidence rather than a stale spreadsheet.',
  },
  {
    id: 'vuln-scanning',
    title: 'Vulnerability scanning every 6 months + penetration testing annually',
    citation: '§ 164.308(a)(1)(ii)(E) proposed (NEW)',
    change:
      'Regulated entities must conduct vulnerability scans at least every six months and penetration tests at least annually, or more frequently if the risk analysis indicates.',
    painForHealthcare:
      'This requirement did not exist before. Many practices — and plenty of multi-location groups — have never run a vulnerability scan. The cost at scale under traditional MSP billing is substantial.',
    osirisCapability:
      'CVE Watch monitors every discovered device across every site against known-CVE databases daily, producing signed evidence bundles per cycle. Penetration testing remains a partner-delivered service but OsirisCare ingests signed reports into the same evidence chain so the auditor sees one cryptographic trail, not a pile of PDFs.',
  },
  {
    id: 'patching',
    title: 'Patching — defined timelines, not "reasonable efforts"',
    citation: '§ 164.312(c)(2) proposed',
    change:
      'Security patches for known vulnerabilities must be applied within timelines defined by the regulated entity\'s risk analysis, with the timelines themselves defensible to an auditor.',
    painForHealthcare:
      'A policy that says "we patch regularly" will fail. Auditors will demand the policy, the log, and the mean-time-to-patch across the entire fleet — which for a multi-site DSO is dozens to hundreds of endpoints.',
    osirisCapability:
      'patching check measures time-to-patch per host per CVE across the entire fleet. Dashboards surface any host exceeding the policy window, and signed evidence records each patch event so the audit trail is intact whether you run 3 endpoints or 300.',
  },
  {
    id: 'incident-response',
    title: 'Incident response — with business-associate coordination',
    citation: '§ 164.308(a)(6)(ii) proposed',
    change:
      'The incident response plan must specifically address coordination with business associates during and after an incident, with documented timelines and named roles.',
    painForHealthcare:
      'Most IR plans list "call IT" and stop there. Coordinating with multiple business associates — EMR vendor, imaging vendor, labs, billing processor — across multiple sites is rarely documented, yet the rule will expect it.',
    osirisCapability:
      'Every remediation step — L1 deterministic, L2 LLM-planned, L3 human-escalated — is captured as an append-only row with actor email, reason, and Ed25519-signed attestation. Cross-BA coordination is recorded as part of the incident chain. Privileged access attestations (three-list lockstep, unbroken chain of custody) ensure the audit story is truthful.',
  },
  {
    id: 'risk-analysis',
    title: 'Risk analysis — written, comprehensive, reviewed annually',
    citation: '§ 164.308(a)(1)(ii)(A) proposed',
    change:
      'Risk analysis must be written, comprehensive (enumerating specific threats, not generic categories), and reviewed annually or after material changes.',
    painForHealthcare:
      'Template risk analyses bought from generic compliance vendors will be flagged. The rule demands specificity to your actual environment — per site, per system, per dataset.',
    osirisCapability:
      'Framework Config surfaces a live risk analysis tied to discovered devices, patching posture, and control coverage across all nine supported frameworks (HIPAA, SOC 2, PCI DSS, NIST CSF, CIS, SOX, GDPR, CMMC, ISO 27001). The risk analysis becomes a live document grounded in real telemetry, not a static template.',
  },
  {
    id: 'contingency-testing',
    title: 'Contingency plan — tested, not just written',
    citation: '§ 164.308(a)(7)(ii)(D) proposed',
    change:
      'Contingency plans (disaster recovery, emergency mode operation) must be tested at least annually, with documented results.',
    painForHealthcare:
      'Written plans are common; tested plans are rare. An untested plan — even a well-written one — becomes a finding under the new rule.',
    osirisCapability:
      'Backup validation checks verify restore-from-backup succeeds on a recurring cadence, producing signed evidence of each successful test per site. Multi-appliance sites additionally validate mesh failover (when one appliance is offline, work redistributes deterministically via hash-ring target assignment) — so the contingency evidence covers both data and control-plane availability.',
  },
];

const FAQ: Array<{ q: string; a: string }> = [
  {
    q: 'When does the 2026 HIPAA Security Rule take effect?',
    a: 'The HHS Office for Civil Rights published the Notice of Proposed Rulemaking (NPRM) on December 27, 2024. Rulemaking typically takes 12–24 months. Final rule adoption is expected in 2026 with a 180-day compliance deadline after finalization. Organizations that wait until the rule is final will have roughly six months to implement technical changes that take most organizations a year.',
  },
  {
    q: 'Does OsirisCare work for multi-site organizations, or only single clinics?',
    a: 'Both. The platform was built with multi-site in mind from day one — backend-authoritative mesh distributes monitoring work across multiple appliances per site, cross-subnet device discovery handles multi-VLAN networks, and fleet-wide dashboards roll up evidence across every location. A single solo clinic runs one appliance; a 20-location DSO runs 20-plus, with deterministic failover via hash-ring target assignment.',
  },
  {
    q: 'Will my existing HIPAA risk analysis satisfy the new rule?',
    a: 'Probably not, if it was templated. The NPRM specifically calls out generic risk analyses and requires specificity to your environment — discovered assets, documented patching posture, evidence of control coverage. Template risk analyses from generic compliance vendors are a frequent finding under the proposed rule.',
  },
  {
    q: 'Do I need a dedicated compliance officer under the new rule?',
    a: 'The NPRM keeps the designated Security Officer requirement and adds more specific duties around workforce training and vendor oversight. A designated person is still required; for small organizations this person does not need to be full-time, and for larger groups the role scales naturally alongside an automated compliance platform that handles the evidence capture.',
  },
  {
    q: 'Is MFA really required for all ePHI access, including on-premise workstations?',
    a: 'Under the proposed rule, yes. The "addressable" classification that previously allowed MFA to be waived with a documented equivalent measure is eliminated. Workforce with routine ePHI access, privileged access, and remote access all require MFA.',
  },
  {
    q: 'How does the platform scale to a 20-location DSO?',
    a: 'Each site runs one or more appliances. Central Command treats the fleet as a single compliance surface — a DSO IT director sees one dashboard that rolls up every control across every location, with per-site drill-down and per-appliance heartbeat. Mesh ring target assignment deterministically distributes monitoring work when a site has multiple appliances, so adding redundancy at a high-value location is an operational decision, not an architectural one.',
  },
  {
    q: 'How is OsirisCare different from Vanta or Drata for HIPAA compliance?',
    a: 'Vanta and Drata are horizontal GRC platforms optimized for SOC 2 audit readiness across many frameworks. OsirisCare is purpose-built for healthcare, installs on-site hardware so PHI scrubbing happens at the egress point, and produces Ed25519-signed, hash-chained evidence bundles that auditors verify independently on their own laptop. For single-framework HIPAA customers OsirisCare is simpler; for multi-framework customers (HIPAA + SOC 2 + ISO 27001) the 9-framework crosswalk maps a single evidence bundle to every applicable control.',
  },
  {
    q: 'What about the OCR enforcement landscape — is this going to affect organizations of all sizes?',
    a: 'Yes. OCR has increased resolution agreements with practices and groups of every size in each of the last three enforcement years. The NPRM explicitly mentions scaling enforcement to reflect entity size, but "scaled" does not mean "exempt." Documented, evidence-backed compliance is the cheapest insurance against a resolution agreement whether you run one clinic or fifty.',
  },
  {
    q: 'Do I need to buy new hardware to comply with the 2026 rule?',
    a: 'Probably not, if your existing endpoints can run BitLocker / FileVault and your firewall supports VLAN tagging. OsirisCare adds one (or more) appliance per site that monitors compliance continuously — it does not replace your EMR, workstations, or existing backup infrastructure. Existing MSPs can deploy alongside without ripping out tools already in production.',
  },
  {
    q: 'Can we verify OsirisCare\'s evidence without trusting OsirisCare?',
    a: 'Yes — that is the point. Every evidence bundle is Ed25519-signed with per-appliance keys, hash-chained (tamper-evident), and OpenTimestamps-anchored to the Bitcoin blockchain. Auditors download a verifier kit (README, verify.sh, chain.json, bundles, public keys, OpenTimestamps proofs) as a ZIP and run verification on their own laptop with no OsirisCare dependency. If we shut down tomorrow, every bundle we ever produced remains verifiable.',
  },
  {
    q: 'How long does deployment take?',
    a: 'A single-site pilot is typically online within 30 minutes of the appliance arriving on the LAN. Multi-site rollouts scale linearly — plug the appliance in, apply the USB install media, and the box auto-provisions on its first checkin with no manual configuration. For a 20-location DSO the full fleet is typically deployed within 2–4 weeks depending on shipping.',
  },
  {
    q: 'What if a site has intermittent internet?',
    a: 'The appliance buffers evidence locally and ships it to Central Command when connectivity recovers. Local status is reachable via a LAN-only diagnostic beacon on port 8443 even when outbound HTTPS is failing — so site staff and visiting IT can verify the box is healthy even during an internet outage.',
  },
];

export const Hipaa2026Update: React.FC = () => {
  useEffect(() => {
    document.title =
      '2026 HIPAA Security Rule Update — Plain-English Guide for Healthcare Organizations | OsirisCare';
    setCanonicalAndDescription(
      'https://www.osiriscare.net/2026-hipaa-update',
      'The 9 requirements in the 2026 HIPAA Security Rule NPRM (HHS, Dec 2024) — MFA, encryption, asset inventory, vulnerability scanning, patching, incident response, risk analysis, contingency testing, network segmentation. For single clinics through multi-site provider networks. Each requirement mapped to a concrete OsirisCare capability.',
    );
  }, []);

  return (
    <MarketingLayout activeNav="2026">
      <JsonLd
        data={{
          '@context': 'https://schema.org',
          '@type': 'Article',
          headline: '2026 HIPAA Security Rule Update — Plain-English Guide for Healthcare Organizations',
          description:
            'Plain-English guide to the 9 requirements in the 2026 HIPAA Security Rule NPRM, mapped to continuous-monitoring capabilities for single clinics through multi-site networks.',
          author: { '@type': 'Organization', name: 'OsirisCare' },
          publisher: {
            '@type': 'Organization',
            name: 'OsirisCare',
            logo: { '@type': 'ImageObject', url: 'https://www.osiriscare.net/og-image.png' },
          },
          datePublished: '2026-04-16',
          dateModified: '2026-04-16',
          mainEntityOfPage: 'https://www.osiriscare.net/2026-hipaa-update',
        }}
      />
      <JsonLd
        data={{
          '@context': 'https://schema.org',
          '@type': 'FAQPage',
          mainEntity: FAQ.map(({ q, a }) => ({
            '@type': 'Question',
            name: q,
            acceptedAnswer: { '@type': 'Answer', text: a },
          })),
        }}
      />

      {/* Hero */}
      <section className="border-b border-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-16 lg:py-24">
          <p className="text-xs uppercase tracking-[0.2em] text-teal-700 font-semibold mb-4">
            Regulatory guide · Updated April 2026
          </p>
          <h1 className="font-display text-4xl lg:text-6xl text-slate-900 leading-tight mb-6">
            The 2026 HIPAA Security Rule changes what{' '}
            <span className="text-teal-700">compliance</span> actually means
            for healthcare organizations.
          </h1>
          <p className="text-lg text-slate-600 leading-relaxed mb-6">
            In December 2024, HHS published the first substantive update
            to the HIPAA Security Rule in over twenty years. Nine
            technical safeguards are being promoted from "addressable"
            to "required," two requirements are entirely new, and the
            rule explicitly requires documented evidence — not
            self-attestation — for every control.
          </p>
          <p className="text-lg text-slate-600 leading-relaxed mb-8">
            This page is the plain-English version for practice managers,
            IT directors at multi-site groups, DSO operations leads, and
            partner MSPs. Each requirement is mapped to a concrete
            monitoring capability that works identically whether you run
            a single-clinic pilot or a fleet of appliances across dozens
            of locations.
          </p>
          <div className="flex items-center gap-4">
            <Link
              to="/signup"
              className="inline-flex items-center text-sm font-semibold px-5 py-3 rounded-lg text-white"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              Start a 90-day pilot →
            </Link>
            <a
              href="https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center text-sm font-medium px-5 py-3 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50"
            >
              Book a fleet-readiness call
            </a>
          </div>
        </div>
      </section>

      {/* Scale positioning — replaces generic "small practices" framing */}
      <section className="bg-slate-50 border-b border-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-12">
          <h2 className="font-display text-2xl text-slate-900 mb-4">
            One platform, every scale
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div>
              <p className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-2 font-body">
                Single clinic
              </p>
              <p className="text-sm text-slate-700 leading-relaxed font-body">
                One appliance on the LAN. Solo-practice owner or
                practice manager as the designated Security Officer.
                Pilot pricing designed for self-selection.
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-2 font-body">
                Multi-location group
              </p>
              <p className="text-sm text-slate-700 leading-relaxed font-body">
                One or more appliances per site, backend-authoritative
                mesh coordination, fleet-wide dashboards with per-site
                drill-down. No per-seat pricing.
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-2 font-body">
                DSO / health-system IT
              </p>
              <p className="text-sm text-slate-700 leading-relaxed font-body">
                Deterministic failover via hash-ring target assignment.
                Cross-subnet discovery. Multi-framework
                (HIPAA + SOC 2 + ISO 27001) single evidence chain.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Quick navigator */}
      <section className="bg-white border-b border-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-10">
          <h2 className="font-display text-xl text-slate-900 mb-4">
            The nine requirements at a glance
          </h2>
          <ol className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm font-body">
            {NPRM_CHANGES.map((c, i) => (
              <li key={c.id}>
                <a href={`#${c.id}`} className="text-teal-700 hover:text-teal-900 hover:underline">
                  {i + 1}. {c.title}
                </a>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Deep dive per requirement */}
      <section>
        <div className="max-w-4xl mx-auto px-6 py-16 space-y-16">
          {NPRM_CHANGES.map((c, i) => (
            <article key={c.id} id={c.id} className="scroll-mt-24">
              <p className="text-xs uppercase tracking-[0.2em] text-slate-400 font-semibold mb-2">
                Requirement {i + 1} · {c.citation}
              </p>
              <h2 className="font-display text-2xl lg:text-3xl text-slate-900 mb-4">
                {c.title}
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-6">
                <div className="md:col-span-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2 font-body">
                    What the rule says
                  </h3>
                  <p className="text-slate-700 leading-relaxed font-body mb-6">{c.change}</p>

                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2 font-body">
                    Why this matters across the fleet
                  </h3>
                  <p className="text-slate-700 leading-relaxed font-body">{c.painForHealthcare}</p>
                </div>

                <aside className="bg-teal-50 rounded-xl p-5 border border-teal-100">
                  <p className="text-xs font-semibold uppercase tracking-wider text-teal-800 mb-2 font-body">
                    How OsirisCare monitors it
                  </p>
                  <p className="text-sm text-teal-900 leading-relaxed font-body">{c.osirisCapability}</p>
                </aside>
              </div>
            </article>
          ))}
        </div>
      </section>

      {/* FAQ section */}
      <section className="bg-slate-50 border-t border-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-16">
          <h2 className="font-display text-3xl text-slate-900 mb-8">Frequently asked</h2>
          <div className="space-y-4">
            {FAQ.map(({ q, a }, i) => (
              <details
                key={i}
                className="bg-white rounded-xl border border-slate-200 px-5 py-4 group"
              >
                <summary className="cursor-pointer font-semibold text-slate-900 font-body">{q}</summary>
                <p className="mt-3 text-slate-700 leading-relaxed font-body">{a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section>
        <div className="max-w-4xl mx-auto px-6 py-20 text-center">
          <h2 className="font-display text-3xl lg:text-4xl text-slate-900 mb-4">
            Be ready before the rule is final.
          </h2>
          <p className="text-lg text-slate-600 leading-relaxed mb-8 max-w-2xl mx-auto">
            Every organization on OsirisCare today is already
            monitoring every control in the NPRM — so when final-rule
            adoption happens, the six-month compliance window is a
            verification exercise, not a project.
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link
              to="/signup"
              className="inline-flex items-center text-base font-semibold px-6 py-3 rounded-lg text-white"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              Start a 90-day pilot →
            </Link>
            <Link
              to="/pricing"
              className="inline-flex items-center text-base font-medium px-6 py-3 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50"
            >
              See fleet pricing
            </Link>
          </div>
          <p className="mt-6 text-xs text-slate-400 font-body">
            Pilot credit applies toward your first month on a paid tier.
            30-day rolling cancellation; no multi-year contract required.
          </p>
        </div>
      </section>
    </MarketingLayout>
  );
};

function setCanonicalAndDescription(url: string, description: string) {
  if (typeof document === 'undefined') return;
  let canonical = document.querySelector('link[rel="canonical"]');
  if (!canonical) {
    canonical = document.createElement('link');
    canonical.setAttribute('rel', 'canonical');
    document.head.appendChild(canonical);
  }
  canonical.setAttribute('href', url);

  let desc = document.querySelector('meta[name="description"]');
  if (!desc) {
    desc = document.createElement('meta');
    desc.setAttribute('name', 'description');
    document.head.appendChild(desc);
  }
  desc.setAttribute('content', description);
}

export default Hipaa2026Update;

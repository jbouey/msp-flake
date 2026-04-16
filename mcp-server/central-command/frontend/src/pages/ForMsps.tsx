import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { MarketingLayout } from '../components/marketing/MarketingLayout';
import { JsonLd } from '../components/marketing/JsonLd';

/**
 * ForMsps — /for-msps
 *
 * Partner-channel landing page. MSPs and compliance consultants who
 * serve healthcare clients are the fastest path to fleet-scale
 * deployments. This page leads with what the mesh + multi-tenant
 * partner portal actually does, not with feel-good margin talk.
 */

const DIFFERENTIATORS: Array<{ title: string; body: string }> = [
  {
    title: 'Multi-tenant by design — every client isolated',
    body:
      'Each healthcare client gets its own site_id, its own per-appliance signing keys, its own RLS-enforced row isolation in Central Command. One partner dashboard; zero shared state across clients. Verified with row-level security policies and privileged chain-of-custody enforcement at the DB layer.',
  },
  {
    title: 'Fleet pricing, not per-seat',
    body:
      'Margins are predictable: a 20% partner margin applies across your book, flat. You quote the client, we invoice you, you invoice the client. Client-level billing portals are available for partners who prefer their clients self-serve through a co-branded flow.',
  },
  {
    title: 'Backend-authoritative mesh — zero config',
    body:
      'When a site gets a second (or fifth) appliance for redundancy, the backend computes target assignments server-side and hash-ring rebalances automatically. No partner ops cycle spent configuring "which appliance handles what" — the substrate handles it.',
  },
  {
    title: 'Cross-subnet discovery',
    body:
      'Healthcare LANs are rarely flat in reality: clinical VLAN, admin VLAN, guest WiFi, IoT, maybe a DMZ for the EMR portal. The appliance discovers devices across VLANs it can reach, and the discovered-device inventory feeds directly into the HIPAA asset-inventory requirement.',
  },
  {
    title: 'Nine frameworks, one evidence chain',
    body:
      'HIPAA, SOC 2, PCI DSS, NIST CSF, CIS, SOX, GDPR, CMMC, ISO 27001. Clients with mixed compliance scope (HIPAA + SOC 2 is common for DSOs, HIPAA + PCI common for imaging centers) run off one evidence bundle with a framework crosswalk — not nine parallel audit programs.',
  },
  {
    title: 'Cryptographic auditor kit',
    body:
      'The auditor downloads a ZIP. The ZIP contains README.md, verify.sh, chain.json, bundles.jsonl, pubkeys.json, and OpenTimestamps proofs. The auditor verifies every signature on their own laptop with no OsirisCare dependency. You stop playing "re-export the report" when the auditor asks follow-up questions.',
  },
  {
    title: 'Self-healing with explicit human-in-loop',
    body:
      'L1 deterministic runbooks fix 70–80% of incidents in under 100ms with no human. L2 LLM-planned fixes catch another 15–20%. L3 escalates to you, the partner, with context. Every remediation writes an Ed25519-signed attestation — you can prove to the client\'s auditor exactly what changed and who authorized it.',
  },
  {
    title: 'Reverse-tunnel-capable appliances',
    body:
      'Appliances phone home via WireGuard to the partner-accessible hub. When a site loses LAN SSH, the reverse tunnel stays up. Break-glass passphrase rotates per-appliance (never MAC-derived, never predictable). Emergency recovery is a fleet order, not a truck roll.',
  },
];

const PARTNER_WORKFLOW: Array<{ step: string; description: string }> = [
  {
    step: 'Provision',
    description:
      'Partner creates the client record in the partner portal, receives a USB image with the client\'s site_id baked in, ships it to the client.',
  },
  {
    step: 'Install',
    description:
      'Client plugs in the appliance, boots from USB, walks away. Install is unattended — the appliance auto-provisions against Central Command on first checkin.',
  },
  {
    step: 'Monitor',
    description:
      'Continuous drift detection, evidence capture, alerting. The partner dashboard surfaces cross-client health at a glance; drill into any client for full detail.',
  },
  {
    step: 'Remediate',
    description:
      'Most incidents self-heal via L1 runbooks. L2 escalations come with LLM-generated plans. L3 reaches you with enough context to act — not a paging-tool page.',
  },
  {
    step: 'Attest',
    description:
      'Client auditor asks for evidence. You send them a ZIP. They verify. The call ends in 20 minutes instead of three follow-ups.',
  },
];

const PARTNER_FAQ: Array<{ q: string; a: string }> = [
  {
    q: 'How does partner billing work?',
    a: 'You quote the client, we invoice you at a 20% partner discount from list, you invoice the client. For partners who prefer direct billing, client-level Stripe Customer Portals are available at /client/billing with 20% margin routed back to the partner automatically.',
  },
  {
    q: 'Can I white-label the client portal?',
    a: 'Partner branding on the client portal (logo, primary color, footer contact) is supported on the Professional tier and above. OsirisCare attribution remains on cryptographic artifacts (evidence bundles, public keys) since those are verified externally.',
  },
  {
    q: 'What happens if a partner relationship ends?',
    a: 'The client keeps the appliance and the evidence chain. They can migrate to another partner, to direct billing, or export the full auditor kit and walk away. No vendor lock on evidence — it is verifiable without OsirisCare infrastructure.',
  },
  {
    q: 'Do you compete with me on my existing clients?',
    a: 'No. OsirisCare does not run a direct-sales motion against partner-owned clients. Client-owner provenance is tracked at the database level; if you brought the client, the client is attributed to you.',
  },
  {
    q: 'How many clients / appliances can one partner manage?',
    a: 'The backend has been tested at fleet-level scale. One partner can manage hundreds of appliances across dozens of clients without operational strain — the mesh + backend-authoritative target assignment + SSE-based dashboard were all designed for that shape.',
  },
  {
    q: 'What if one of my clients fails a drift check at 2am?',
    a: 'L1 deterministic runbooks run first (in under 100ms, no human). If L1 cannot resolve it, L2 LLM planner produces a proposed remediation — shadow-mode by default, or enforce-mode for partners who have approved the class of action. L3 pages you with full context only if neither L1 nor L2 could resolve it. The sleep-at-night curve improves substantially.',
  },
];

export const ForMsps: React.FC = () => {
  useEffect(() => {
    document.title = 'For MSPs and Compliance Partners — OsirisCare';
    setCanonicalAndDescription(
      'https://www.osiriscare.net/for-msps',
      'Partner channel for MSPs and compliance consultants serving healthcare. Multi-tenant isolation, backend-authoritative mesh, cryptographic auditor kits, 9-framework support, 20% partner margin. Fleet pricing from single-clinic to DSO scale.',
    );
  }, []);

  return (
    <MarketingLayout activeNav="msps">
      <JsonLd
        data={{
          '@context': 'https://schema.org',
          '@type': 'WebPage',
          name: 'For MSPs and Compliance Partners',
          description:
            'Partner channel for MSPs and compliance consultants serving healthcare practices and multi-site provider networks.',
          url: 'https://www.osiriscare.net/for-msps',
          isPartOf: { '@type': 'WebSite', name: 'OsirisCare', url: 'https://www.osiriscare.net' },
        }}
      />

      <section className="border-b border-slate-100">
        <div className="max-w-5xl mx-auto px-6 py-16 lg:py-24">
          <p className="text-xs uppercase tracking-[0.2em] text-teal-700 font-semibold mb-4">
            Partner channel
          </p>
          <h1 className="font-display text-4xl lg:text-6xl text-slate-900 leading-tight mb-6">
            A compliance platform that{' '}
            <span className="text-teal-700">scales with your book</span>.
          </h1>
          <p className="text-lg text-slate-600 leading-relaxed max-w-3xl">
            OsirisCare is built for MSPs and compliance consultants who
            serve healthcare — from a solo practice on a pilot to a
            20-location DSO. Multi-tenant isolation is enforced at the
            database layer. Mesh coordination is server-side. Evidence
            is cryptographically verifiable. And the margin is
            predictable.
          </p>
          <div className="flex items-center gap-4 mt-8">
            <a
              href="https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center text-sm font-semibold px-5 py-3 rounded-lg text-white"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              Request partner demo →
            </a>
            <Link
              to="/partner/login"
              className="inline-flex items-center text-sm font-medium px-5 py-3 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50"
            >
              Partner sign in
            </Link>
          </div>
        </div>
      </section>

      {/* Differentiators */}
      <section className="bg-slate-50 border-b border-slate-100">
        <div className="max-w-5xl mx-auto px-6 py-16">
          <h2 className="font-display text-3xl text-slate-900 mb-10">
            What you actually get
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-8">
            {DIFFERENTIATORS.map((d) => (
              <div key={d.title}>
                <h3 className="font-display text-xl text-slate-900 mb-2">{d.title}</h3>
                <p className="text-slate-600 leading-relaxed text-sm font-body">{d.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Workflow */}
      <section>
        <div className="max-w-5xl mx-auto px-6 py-20">
          <h2 className="font-display text-3xl text-slate-900 mb-10">
            Five-step partner workflow
          </h2>
          <ol className="space-y-6">
            {PARTNER_WORKFLOW.map((s, i) => (
              <li key={s.step} className="flex gap-6">
                <div
                  className="flex-shrink-0 w-10 h-10 rounded-full text-white flex items-center justify-center font-semibold text-sm font-body"
                  style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
                >
                  {i + 1}
                </div>
                <div>
                  <h3 className="font-display text-xl text-slate-900 mb-1">{s.step}</h3>
                  <p className="text-slate-600 leading-relaxed font-body">{s.description}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Pricing callout */}
      <section className="bg-slate-900 text-white">
        <div className="max-w-5xl mx-auto px-6 py-16">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-10 items-center">
            <div>
              <h2 className="font-display text-3xl text-white mb-4">
                Fleet pricing, flat margin.
              </h2>
              <p className="text-slate-300 leading-relaxed font-body mb-6">
                20% partner margin applies across the Essentials,
                Professional, and Enterprise tiers. No per-seat
                metering, no surprise overage fees, no volume
                renegotiation calls every quarter.
              </p>
              <Link
                to="/pricing"
                className="inline-flex items-center text-sm font-semibold text-teal-300 hover:text-teal-100"
              >
                See full tiers →
              </Link>
            </div>
            <div className="bg-slate-800 rounded-xl p-6 border border-slate-700">
              <p className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-3 font-body">
                Example: 10-location dental DSO
              </p>
              <div className="text-slate-200 font-body text-sm space-y-2 leading-relaxed">
                <div>10 appliances (Professional tier)</div>
                <div>Bundled partner margin · 20%</div>
                <div>Multi-framework: HIPAA + PCI DSS + SOC 2</div>
                <div>Fleet-wide auditor kit · unlimited exports</div>
              </div>
              <p className="text-slate-400 text-xs mt-5 font-body">
                Talk to us for a specific quote scoped to your book's
                location count and compliance scope.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="bg-slate-50">
        <div className="max-w-4xl mx-auto px-6 py-16">
          <h2 className="font-display text-3xl text-slate-900 mb-8">
            Partner FAQ
          </h2>
          <div className="space-y-4">
            {PARTNER_FAQ.map(({ q, a }, i) => (
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
          <h2 className="font-display text-3xl lg:text-4xl text-slate-900 mb-6">
            Ready to see the partner portal?
          </h2>
          <div className="flex items-center justify-center gap-4">
            <a
              href="https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center text-base font-semibold px-6 py-3 rounded-lg text-white"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              Book a partner demo →
            </a>
            <Link
              to="/2026-hipaa-update"
              className="inline-flex items-center text-base font-medium px-6 py-3 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50"
            >
              Review 2026 HIPAA readiness
            </Link>
          </div>
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

export default ForMsps;

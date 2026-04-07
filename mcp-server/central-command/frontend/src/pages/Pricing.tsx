import React from 'react';
import { Link } from 'react-router-dom';
import { OsirisCareLeaf } from '../components/shared';
import { BRANDING } from '../constants';

const CheckIcon: React.FC = () => (
  <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="#14A89E" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
  </svg>
);

const DashIcon: React.FC = () => (
  <svg className="w-5 h-5 flex-shrink-0 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
  </svg>
);

interface TierProps {
  name: string;
  price: string;
  description: string;
  features: { text: string; included: boolean }[];
  cta: string;
  ctaHref: string;
  highlighted?: boolean;
}

const TierCard: React.FC<TierProps> = ({ name, price, description, features, cta, ctaHref, highlighted }) => (
  <div
    className={`relative rounded-2xl p-8 flex flex-col ${
      highlighted
        ? 'border-2 border-teal-500 bg-white shadow-xl'
        : 'border border-slate-200 bg-white'
    }`}
  >
    {highlighted && (
      <div
        className="absolute -top-4 left-1/2 -translate-x-1/2 px-4 py-1 rounded-full text-xs font-semibold text-white font-body"
        style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
      >
        Most Popular
      </div>
    )}

    <div className="mb-6">
      <h3 className="text-xl font-semibold text-slate-900 font-body">{name}</h3>
      <div className="mt-4 flex items-baseline">
        <span className="text-4xl font-bold text-slate-900 tabular-nums font-body">{price}</span>
        <span className="ml-1 text-sm text-slate-500 font-body">/month</span>
      </div>
      <p className="mt-3 text-sm text-slate-500 leading-relaxed font-body">{description}</p>
    </div>

    <ul className="space-y-3 mb-8 flex-1">
      {features.map((f, i) => (
        <li key={i} className="flex items-start gap-3">
          {f.included ? <CheckIcon /> : <DashIcon />}
          <span className={`text-sm ${f.included ? 'text-slate-700' : 'text-slate-400'} font-body`}>
            {f.text}
          </span>
        </li>
      ))}
    </ul>

    <a
      href={ctaHref}
      target="_blank"
      rel="noopener noreferrer"
      className={`block text-center px-6 py-3 rounded-lg text-sm font-semibold transition-all font-body ${
        highlighted
          ? 'text-white'
          : 'text-teal-700 border-2 border-teal-500 hover:bg-teal-50'
      }`}
      style={highlighted ? {
        background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)',
        boxShadow: '0 2px 12px rgba(20, 168, 158, 0.3)',
      } : undefined}
    >
      {cta}
    </a>
  </div>
);

export const Pricing: React.FC = () => {
  const tiers: TierProps[] = [
    {
      name: 'Essentials',
      price: '$499',
      description: 'Full compliance monitoring for practices that need visibility and basic auto-healing.',
      features: [
        { text: 'All 59 compliance checks (Windows, Linux, macOS)', included: true },
        { text: 'L1 deterministic auto-healing', included: true },
        { text: 'Ed25519 signed evidence bundles', included: true },
        { text: 'Blockchain timestamping (OpenTimestamps)', included: true },
        { text: 'Client compliance portal', included: true },
        { text: 'Monthly compliance monitoring summary', included: true },
        { text: 'Email notifications on issues + healing', included: true },
        { text: 'Top 50 remediation runbooks', included: true },
        { text: 'L2 LLM-powered healing', included: false },
        { text: 'Full runbook library (288)', included: false },
        { text: 'Partner fleet management portal', included: false },
        { text: 'Priority L3 escalation (8h SLA)', included: false },
        { text: 'Audit preparation support', included: false },
      ],
      cta: 'Schedule a Demo',
      ctaHref: 'https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard',
    },
    {
      name: 'Professional',
      price: '$799',
      description: 'Complete healing pipeline with LLM-powered remediation and full compliance packets.',
      features: [
        { text: 'All 59 compliance checks (Windows, Linux, macOS)', included: true },
        { text: 'L1 deterministic auto-healing', included: true },
        { text: 'Ed25519 signed evidence bundles', included: true },
        { text: 'Blockchain timestamping (OpenTimestamps)', included: true },
        { text: 'Client compliance portal', included: true },
        { text: 'Full monthly compliance packets', included: true },
        { text: 'Email notifications on issues + healing', included: true },
        { text: 'Full runbook library (288 runbooks)', included: true },
        { text: 'L2 LLM-powered healing', included: true },
        { text: 'Partner fleet management portal', included: true },
        { text: 'Peer-witnessed evidence (multi-appliance)', included: true },
        { text: 'Priority L3 escalation (8h SLA)', included: true },
        { text: 'Audit preparation support', included: false },
      ],
      cta: 'Schedule a Demo',
      ctaHref: 'https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard',
      highlighted: true,
    },
    {
      name: 'Enterprise',
      price: '$1,299',
      description: 'Dedicated support, custom runbooks, and audit preparation for larger practices.',
      features: [
        { text: 'All 59 compliance checks (Windows, Linux, macOS)', included: true },
        { text: 'L1 deterministic auto-healing', included: true },
        { text: 'Ed25519 signed evidence bundles', included: true },
        { text: 'Blockchain timestamping (OpenTimestamps)', included: true },
        { text: 'Client compliance portal', included: true },
        { text: 'Full monthly compliance packets', included: true },
        { text: 'Email notifications on issues + healing', included: true },
        { text: 'Full runbook library + custom runbooks', included: true },
        { text: 'L2 LLM-powered healing', included: true },
        { text: 'Partner fleet management portal', included: true },
        { text: 'Peer-witnessed evidence (multi-appliance)', included: true },
        { text: 'Dedicated L3 escalation (4h SLA)', included: true },
        { text: 'Audit preparation support (4 hrs/quarter)', included: true },
      ],
      cta: 'Schedule a Demo',
      ctaHref: 'https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard',
    },
  ];

  return (
    <div className="min-h-screen bg-white" style={{ fontFamily: "'DM Sans', 'Helvetica Neue', system-ui, sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Serif+Display&display=swap');
        .font-display { font-family: 'DM Serif Display', Georgia, serif; }
        .font-body { font-family: 'DM Sans', 'Helvetica Neue', system-ui, sans-serif; }
      `}</style>

      {/* Nav */}
      <nav className="sticky top-0 z-50 border-b border-slate-100 bg-white/95">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3">
            <div
              className="w-9 h-9 rounded-lg flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              <OsirisCareLeaf className="w-5 h-5" color="white" />
            </div>
            <span className="text-lg font-semibold text-slate-900 tracking-tight font-body">
              {BRANDING.name}
            </span>
          </Link>
          <div className="flex items-center gap-4">
            <Link to="/" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">
              Back to Home
            </Link>
            <a
              href="https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium px-4 py-2 rounded-lg text-white transition-all font-body"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              Book a Demo
            </a>
          </div>
        </div>
      </nav>

      {/* Header */}
      <section className="pt-20 pb-8 text-center">
        <div className="max-w-4xl mx-auto px-6">
          <p className="text-sm font-semibold uppercase tracking-widest mb-4 font-body" style={{ color: '#0d9488' }}>
            Pricing
          </p>
          <h1 className="font-display text-4xl md:text-5xl text-slate-900 mb-4">
            75% less than traditional<br />MSP compliance
          </h1>
          <p className="text-lg text-slate-500 max-w-2xl mx-auto font-body font-light">
            Every tier includes full compliance scanning and evidence-grade compliance monitoring.
            No per-device fees. No hidden costs. Annual contracts with monthly billing.
          </p>
        </div>
      </section>

      {/* Tiers */}
      <section className="pb-20">
        <div className="max-w-6xl mx-auto px-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {tiers.map((tier, i) => (
              <TierCard key={i} {...tier} />
            ))}
          </div>
        </div>
      </section>

      {/* Pilot */}
      <section className="py-16 border-t border-slate-100">
        <div className="max-w-3xl mx-auto px-6 text-center">
          <h2 className="text-2xl font-semibold text-slate-900 mb-4 font-body">
            90-Day Pilot — $299/month
          </h2>
          <p className="text-sm text-slate-500 leading-relaxed mb-6 font-body">
            Not ready to commit? Start with a 90-day pilot at $299/month.
            Full Essentials tier access with your own on-premise appliance.
            After 90 days, upgrade to a full plan or the appliance comes back. No risk.
          </p>
          <a
            href="https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center px-6 py-3 text-sm font-semibold rounded-lg text-teal-700 border-2 border-teal-500 hover:bg-teal-50 transition-all font-body"
          >
            Start a Pilot
          </a>
        </div>
      </section>

      {/* Comparison */}
      <section className="py-16 bg-slate-50">
        <div className="max-w-4xl mx-auto px-6">
          <h2 className="text-2xl font-semibold text-slate-900 mb-8 text-center font-body">
            How we compare
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="text-left py-3 px-4 font-semibold text-slate-900 font-body">Solution</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-900 font-body">Monthly Cost</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-900 font-body">What You Get</th>
                </tr>
              </thead>
              <tbody className="text-slate-600 font-body">
                <tr className="border-b border-slate-100">
                  <td className="py-3 px-4">Traditional MSP Compliance</td>
                  <td className="py-3 px-4">$1,500 – $5,000</td>
                  <td className="py-3 px-4">Manual audits, quarterly reviews, reactive</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-3 px-4">Checklist Tools (Compliancy Group, etc.)</td>
                  <td className="py-3 px-4">$300 – $800</td>
                  <td className="py-3 px-4">Self-assessment checklists, no automation</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-3 px-4">Basic RMM (ConnectWise, Datto)</td>
                  <td className="py-3 px-4">$50 – $150</td>
                  <td className="py-3 px-4">Monitoring only, no compliance, no evidence</td>
                </tr>
                <tr className="border-b border-slate-200" style={{ backgroundColor: 'rgba(20, 168, 158, 0.05)' }}>
                  <td className="py-3 px-4 font-semibold" style={{ color: '#0d9488' }}>OsirisCare</td>
                  <td className="py-3 px-4 font-semibold" style={{ color: '#0d9488' }}>$499 – $1,299</td>
                  <td className="py-3 px-4 font-semibold" style={{ color: '#0d9488' }}>Continuous scanning, auto-healing, evidence-grade bundles, blockchain timestamps</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* PHI-Free callout */}
      <section className="py-16 border-t border-slate-100">
        <div className="max-w-3xl mx-auto px-6 text-center">
          <div
            className="w-12 h-12 rounded-xl flex items-center justify-center mx-auto mb-6"
            style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
          >
            <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <h3 className="text-xl font-semibold text-slate-900 mb-3 font-body">PHI is scrubbed at the appliance before transmission</h3>
          <p className="text-sm text-slate-500 leading-relaxed font-body">
            All data is scrubbed of Protected Health Information at the on-premise appliance
            before transmission. Our central infrastructure is engineered to be PHI-scrubbed architecture —
            reducing your vendor risk and simplifying your compliance posture.
          </p>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-slate-100 py-8">
        <div className="max-w-7xl mx-auto px-6 flex items-center justify-between">
          <p className="text-xs text-slate-400 font-body">
            &copy; {new Date().getFullYear()} {BRANDING.name}. All rights reserved.
          </p>
          <div className="flex gap-6">
            <Link to="/legal/privacy" className="text-xs text-slate-400 hover:text-slate-700 font-body">Privacy</Link>
            <Link to="/legal/terms" className="text-xs text-slate-400 hover:text-slate-700 font-body">Terms</Link>
            <Link to="/legal/baa" className="text-xs text-slate-400 hover:text-slate-700 font-body">BAA</Link>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default Pricing;

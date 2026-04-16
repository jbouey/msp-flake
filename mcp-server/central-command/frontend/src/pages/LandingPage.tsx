import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { OsirisCareLeaf } from '../components/shared';
import { DISCLAIMERS, BRANDING } from '../constants';

/**
 * Since index.html no longer ships a hardcoded <link rel="canonical">
 * (every SPA route sets its own to avoid cross-route canonical collision),
 * the homepage must explicitly declare itself as the canonical for "/"
 * on mount. This mirrors the setCanonicalAndDescription helper used by
 * the marketing cluster (Hipaa2026Update.tsx etc.).
 */
function setHomepageCanonical() {
  if (typeof document === 'undefined') return;
  let canonical = document.querySelector('link[rel="canonical"]');
  if (!canonical) {
    canonical = document.createElement('link');
    canonical.setAttribute('rel', 'canonical');
    document.head.appendChild(canonical);
  }
  canonical.setAttribute('href', 'https://www.osiriscare.net/');
}

/* ─────────────────────────────────────────────────────────
   OsirisCare Landing Page
   Aesthetic: Clinical Precision — medical-journal typography,
   graph-paper subtlety, surgeon's-mark teal accents.
   ───────────────────────────────────────────────────────── */

const DotGrid: React.FC<{ className?: string }> = ({ className = '' }) => (
  <div
    className={`absolute inset-0 pointer-events-none ${className}`}
    style={{
      backgroundImage: 'radial-gradient(circle, #cbd5e1 0.5px, transparent 0.5px)',
      backgroundSize: '24px 24px',
      opacity: 0.3,
    }}
  />
);

const SectionDivider: React.FC = () => (
  <div className="max-w-7xl mx-auto px-6">
    <div className="h-px bg-gradient-to-r from-transparent via-slate-200 to-transparent" />
  </div>
);

export const LandingPage: React.FC = () => {
  useEffect(() => {
    setHomepageCanonical();
  }, []);

  return (
    <div className="min-h-screen bg-white" style={{ fontFamily: "'DM Sans', 'Helvetica Neue', system-ui, sans-serif" }}>
      {/* Google Fonts — DM Sans for body, DM Serif Display for headings */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,400&family=DM+Serif+Display&display=swap');
        .font-display { font-family: 'DM Serif Display', Georgia, serif; }
        .font-body { font-family: 'DM Sans', 'Helvetica Neue', system-ui, sans-serif; }
        @keyframes ecgDraw {
          0% { stroke-dashoffset: 1; opacity: 0.06; }
          15% { opacity: 0.2; }
          50% { stroke-dashoffset: 0; opacity: 0.15; }
          65% { opacity: 0.2; }
          100% { stroke-dashoffset: -1; opacity: 0.06; }
        }
        @keyframes ecgGlow {
          0%, 100% { opacity: 0.7; }
          30% { opacity: 1; }
          60% { opacity: 0.85; }
        }
      `}</style>

      {/* ═══════════════ NAV ═══════════════ */}
      <nav className="sticky top-0 z-50 border-b border-slate-100 bg-white/95">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              className="w-9 h-9 rounded-lg flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              <OsirisCareLeaf className="w-5 h-5" color="white" />
            </div>
            <span className="text-lg font-semibold text-slate-900 tracking-tight font-body">
              OsirisCare
            </span>
          </div>

          <div className="hidden md:flex items-center gap-7 text-sm font-medium text-slate-500 font-body">
            <Link to="/2026-hipaa-update" className="hover:text-slate-900 transition-colors">2026 HIPAA Rule</Link>
            <Link to="/for-msps" className="hover:text-slate-900 transition-colors">For MSPs</Link>
            <a href="#how-it-works" className="hover:text-slate-900 transition-colors">How It Works</a>
            <Link to="/pricing" className="hover:text-slate-900 transition-colors">Pricing</Link>
            <Link to="/blog" className="hover:text-slate-900 transition-colors">Blog</Link>
          </div>

          <div className="flex items-center gap-3">
            <a
              href="https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard"
              target="_blank"
              rel="noopener noreferrer"
              className="hidden sm:inline-flex text-sm font-medium px-4 py-2 rounded-lg border border-teal-600 text-teal-700 hover:bg-teal-50 transition-all font-body"
            >
              Book a Demo
            </a>
            <Link
              to="/client/login"
              className="hidden sm:inline-flex text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors font-body"
            >
              Client Sign In
            </Link>
            <Link
              to="/partner/login"
              className="text-sm font-medium px-4 py-2 rounded-lg text-white transition-all font-body"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              Partner Login
            </Link>
          </div>
        </div>
      </nav>

      {/* ═══════════════ HERO ═══════════════ */}
      <section className="relative overflow-hidden">
        <DotGrid className="opacity-20" />

        <div className="relative max-w-7xl mx-auto px-6 pt-24 pb-20 md:pt-32 md:pb-28">
          <div className="max-w-3xl">
            {/* Logo mark */}
            <div
              className="w-16 h-16 rounded-2xl flex items-center justify-center mb-10"
              style={{
                background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)',
                boxShadow: '0 4px 24px rgba(20, 168, 158, 0.25)',
              }}
            >
              <OsirisCareLeaf className="w-9 h-9" color="white" />
            </div>

            {/* 2026-Ready ribbon — visible tag; links to the full NPRM page */}
            <Link
              to="/2026-hipaa-update"
              className="inline-flex items-center gap-2 mb-6 px-3 py-1.5 rounded-full bg-teal-50 border border-teal-200 text-xs font-semibold text-teal-800 tracking-wider uppercase hover:bg-teal-100 transition-colors font-body"
            >
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-teal-600" />
              2026-Ready HIPAA Platform · NPRM mapped
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </Link>

            <h1 className="font-display text-4xl md:text-5xl lg:text-6xl text-slate-900 leading-[1.1] mb-6">
              Always audit-ready.{' '}
              <span className="block" style={{ color: '#0d9488' }}>With proof — not paperwork.</span>
            </h1>

            <p className="text-lg md:text-xl text-slate-500 leading-relaxed max-w-2xl mb-4 font-body font-light">
              Continuous HIPAA compliance for healthcare — from a single clinic
              to a multi-site provider network or DSO. Cryptographically
              verifiable evidence your auditor confirms on their own laptop.
            </p>
            <p className="text-lg md:text-xl text-slate-600 leading-relaxed max-w-2xl mb-10 font-body font-medium">
              Built for the 2026 HIPAA Security Rule. Nine controls monitored continuously.
            </p>

            <div className="flex flex-col sm:flex-row gap-4">
              <Link
                to="/signup"
                className="inline-flex items-center justify-center px-6 py-3.5 text-sm font-semibold rounded-lg text-white transition-all font-body"
                style={{
                  background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)',
                  boxShadow: '0 2px 12px rgba(20, 168, 158, 0.3)',
                }}
              >
                Start a 90-day pilot
                <svg className="w-4 h-4 ml-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                </svg>
              </Link>
              <Link
                to="/2026-hipaa-update"
                className="inline-flex items-center justify-center px-6 py-3.5 text-sm font-semibold rounded-lg text-slate-700 border border-slate-200 hover:border-slate-300 hover:bg-slate-50 transition-all font-body"
              >
                Read the 2026 HIPAA guide
                <svg className="w-4 h-4 ml-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                </svg>
              </Link>
              <a
                href="https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center px-6 py-3.5 text-sm font-semibold rounded-lg text-teal-700 border-2 border-teal-500 hover:bg-teal-50 transition-all font-body"
              >
                Schedule a Demo
                <svg className="w-4 h-4 ml-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              </a>
            </div>

            <p className="text-xs text-slate-400 mt-6 font-body">
              Trusted from solo clinics to multi-location groups and DSOs. Nine frameworks supported:
              HIPAA, SOC 2, PCI DSS, NIST CSF, CIS, SOX, GDPR, CMMC, ISO 27001.
            </p>
          </div>

          {/* Decorative element — animated vital-sign line */}
          <div className="hidden lg:block absolute right-12 top-32 w-72" style={{
            animation: 'ecgGlow 3.5s ease-in-out infinite',
          }}>
            <svg viewBox="0 0 320 128" fill="none" className="w-full">
              <path
                d="M0 64 H80 L96 20 L112 108 L128 40 L144 88 L160 64 H320"
                stroke="#14A89E"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                pathLength={1}
                style={{
                  strokeDasharray: 1,
                  strokeDashoffset: 1,
                  animation: 'ecgDraw 3s ease-in-out infinite',
                }}
              />
            </svg>
          </div>
        </div>
      </section>

      <SectionDivider />

      {/* ═══════════════ 2026 HIPAA NPRM CALLOUT ═══════════════ */}
      <section className="py-16 md:py-20 bg-teal-50/40 border-y border-teal-100/60">
        <div className="max-w-7xl mx-auto px-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 items-center">
            <div className="md:col-span-1">
              <p className="text-xs font-semibold uppercase tracking-[0.2em] mb-3 font-body" style={{ color: '#0d9488' }}>
                2026 HIPAA Security Rule
              </p>
              <h2 className="font-display text-2xl md:text-3xl text-slate-900 mb-4 leading-snug">
                Built for the new rule —<br />not retrofitted later.
              </h2>
              <p className="text-slate-600 font-body text-sm leading-relaxed mb-5">
                HHS published the first substantive Security Rule update in over twenty years
                in December 2024. Nine controls move from "addressable" to "required." We
                monitor every one of them today.
              </p>
              <Link
                to="/2026-hipaa-update"
                className="inline-flex items-center text-sm font-semibold text-teal-700 hover:text-teal-900 font-body"
              >
                Read the full 9-requirement guide
                <svg className="w-4 h-4 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                </svg>
              </Link>
            </div>
            <div className="md:col-span-2 grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm font-body">
              {[
                { k: 'Multi-factor authentication', v: 'required, continuously verified' },
                { k: 'Encryption at rest + in transit', v: 'BitLocker, FileVault, LUKS, TLS' },
                { k: 'Network segmentation', v: 'cross-subnet device discovery' },
                { k: 'Asset inventory', v: 'annual review, signed' },
                { k: 'Vulnerability scans / 6 mo', v: 'NEW in NPRM' },
                { k: 'Penetration test / year', v: 'NEW in NPRM' },
                { k: 'Patching timelines', v: 'measured, per host, per CVE' },
                { k: 'Incident response + BA coord', v: 'append-only attested chain' },
                { k: 'Tested contingency plan', v: 'backup validation on cadence' },
              ].map((c) => (
                <div key={c.k} className="p-3 rounded-lg bg-white border border-teal-100">
                  <div className="text-xs font-semibold text-slate-900">{c.k}</div>
                  <div className="text-[11px] text-slate-500 mt-1 leading-snug">{c.v}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <SectionDivider />

      {/* ═══════════════ ONE PLATFORM · EVERY SCALE ═══════════════ */}
      <section className="py-16 md:py-20">
        <div className="max-w-7xl mx-auto px-6">
          <div className="max-w-3xl mx-auto text-center mb-12">
            <p className="text-sm font-semibold uppercase tracking-widest mb-4 font-body" style={{ color: '#0d9488' }}>
              One platform · every scale
            </p>
            <h2 className="font-display text-3xl md:text-4xl text-slate-900 mb-4">
              From single clinics to multi-site DSOs
            </h2>
            <p className="text-lg text-slate-500 font-body font-light leading-relaxed">
              Backend-authoritative mesh coordination means adding a location is
              provisioning, not architecture. Add a second appliance at a flagship
              site for redundancy — hash-ring target assignment rebalances
              automatically when one is offline.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {[
              {
                label: 'Single clinic',
                head: 'One appliance · owner-led',
                body: '90-day pilot at $299. Practice manager as the designated Security Officer. Most solo clinics online within 30 min of the appliance arriving.',
              },
              {
                label: 'Multi-location group',
                head: 'One appliance per site',
                body: 'Fleet-wide dashboard with per-site drill-down. Cross-subnet discovery handles multi-VLAN healthcare LANs. No per-seat metering.',
              },
              {
                label: 'DSO / health-system IT',
                head: 'Multiple appliances per site',
                body: 'Hash-ring deterministic failover. Nine-framework crosswalk (HIPAA, SOC 2, PCI, NIST CSF, CIS, SOX, GDPR, CMMC, ISO 27001). One evidence chain for all.',
              },
            ].map((tier) => (
              <div key={tier.label} className="p-6 rounded-xl border border-slate-100 bg-white">
                <p className="text-xs uppercase tracking-widest text-slate-400 font-semibold font-body mb-2">{tier.label}</p>
                <h3 className="font-display text-xl text-slate-900 mb-3">{tier.head}</h3>
                <p className="text-sm text-slate-500 leading-relaxed font-body">{tier.body}</p>
              </div>
            ))}
          </div>

          <div className="text-center mt-10">
            <Link
              to="/for-msps"
              className="inline-flex items-center text-sm font-semibold text-teal-700 hover:text-teal-900 font-body"
            >
              For MSPs and compliance partners — fleet pricing + 20% partner margin
              <svg className="w-4 h-4 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
              </svg>
            </Link>
          </div>
        </div>
      </section>

      <SectionDivider />

      {/* ═══════════════ THE PROBLEM ═══════════════ */}
      <section className="py-20 md:py-28">
        <div className="max-w-7xl mx-auto px-6">
          <div className="max-w-3xl mx-auto text-center mb-16">
            <p className="text-sm font-semibold uppercase tracking-widest mb-4 font-body" style={{ color: '#0d9488' }}>
              The challenge
            </p>
            <h2 className="font-display text-3xl md:text-4xl text-slate-900 mb-6">
              Compliance issues don't wait for your next audit
            </h2>
            <p className="text-lg text-slate-500 leading-relaxed font-body font-light">
              HIPAA compliance is a continuous obligation, not an annual checkbox.
              Multi-site groups and DSOs face compounded complexity: per-site
              drift, cross-location incident coordination, and audit-trail
              reconciliation at scale.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {[
              {
                num: '01',
                title: 'Fleet drift',
                desc: 'Controls drift per location. A 20-location DSO has, statistically, multiple sites out of compliance on multiple controls at any given moment — and the regional IT director cannot see it.',
              },
              {
                num: '02',
                title: 'Evidence fragmentation',
                desc: 'Each site tracks compliance differently. When the auditor arrives, reconciling fifteen locations of screenshots, logs, and policy docs is a weeks-long project.',
              },
              {
                num: '03',
                title: 'Silent failures',
                desc: 'Configuration changes, missed patches, policy deviations — silent failures that accumulate undetected and become audit findings across every site at once.',
              },
              {
                num: '04',
                title: 'Trust-the-vendor auditing',
                desc: 'Platform-signed reports look authoritative — until the vendor\'s integrity is questioned. Auditors are migrating to independently verifiable evidence as the new bar.',
              },
            ].map((item) => (
              <div key={item.num} className="p-6 rounded-xl border border-slate-100 bg-slate-50/50">
                <span className="text-xs font-semibold tracking-wider text-slate-300 font-body">{item.num}</span>
                <h3 className="text-base font-semibold text-slate-900 mt-3 mb-2 font-body">{item.title}</h3>
                <p className="text-sm text-slate-500 leading-relaxed font-body">{item.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <SectionDivider />

      {/* ═══════════════ HOW IT WORKS ═══════════════ */}
      <section id="how-it-works" className="relative py-20 md:py-28 overflow-hidden">
        <DotGrid className="opacity-10" />

        <div className="relative max-w-7xl mx-auto px-6">
          <div className="max-w-3xl mx-auto text-center mb-16">
            <p className="text-sm font-semibold uppercase tracking-widest mb-4 font-body" style={{ color: '#0d9488' }}>
              How it works
            </p>
            <h2 className="font-display text-3xl md:text-4xl text-slate-900 mb-6">
              Observe. Respond. Attest.
            </h2>
            <p className="text-lg text-slate-500 leading-relaxed font-body font-light">
              A continuous compliance workflow designed to support your HIPAA
              program — from detection through documentation.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {/* Observe */}
            <div className="relative p-8 rounded-2xl border border-slate-100 bg-white">
              <div
                className="w-12 h-12 rounded-xl flex items-center justify-center mb-6"
                style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
              >
                <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-slate-900 mb-3 font-body">Observe</h3>
              <p className="text-sm text-slate-500 leading-relaxed font-body">
                Continuous monitoring of your infrastructure for compliance issues.
                Configuration state, access controls, encryption status, and audit
                policies — captured as evidence-grade logs with tamper-evident
                hash chains.
              </p>
            </div>

            {/* Respond */}
            <div className="relative p-8 rounded-2xl border border-slate-100 bg-white">
              <div
                className="w-12 h-12 rounded-xl flex items-center justify-center mb-6"
                style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
              >
                <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-slate-900 mb-3 font-body">Respond</h3>
              <p className="text-sm text-slate-500 leading-relaxed font-body">
                Three-tier response framework. Deterministic rules handle
                routine issues automatically. Advanced analysis addresses
                complex scenarios. Critical decisions escalate to your team
                for human-authorized action.
              </p>
            </div>

            {/* Attest */}
            <div className="relative p-8 rounded-2xl border border-slate-100 bg-white">
              <div
                className="w-12 h-12 rounded-xl flex items-center justify-center mb-6"
                style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
              >
                <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-slate-900 mb-3 font-body">Attest</h3>
              <p className="text-sm text-slate-500 leading-relaxed font-body">
                Proof of compliance generated automatically — not assembled
                before an audit. Cryptographically signed evidence bundles
                with blockchain timestamps. Every action recorded, every
                state change documented, available on demand.
              </p>
            </div>
          </div>
        </div>
      </section>

      <SectionDivider />

      {/* ═══════════════ FOR PRACTICES ═══════════════ */}
      <section id="practices" className="py-20 md:py-28">
        <div className="max-w-7xl mx-auto px-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-center">
            <div>
              <p className="text-sm font-semibold uppercase tracking-widest mb-4 font-body" style={{ color: '#0d9488' }}>
                For healthcare practices
              </p>
              <h2 className="font-display text-3xl md:text-4xl text-slate-900 mb-6">
                Your compliance posture,<br />always visible
              </h2>
              <p className="text-lg text-slate-500 leading-relaxed mb-8 font-body font-light">
                Know your compliance status without waiting for reports or
                chasing updates. Direct access to your compliance data through
                the OsirisCare portal — see what's passing, what was fixed,
                and what needs attention, right now.
              </p>

              <ul className="space-y-4 mb-10">
                {[
                  'Real-time compliance dashboard with current check status',
                  'Monthly compliance monitoring summaries with control-level detail',
                  'Evidence bundles with hash-chain integrity and blockchain timestamps',
                  'Notifications when issues are detected and when healing completes',
                  'Healing activity logs — see exactly what was remediated and when',
                  'No lock-in — your data is yours, and you can change partners anytime',
                ].map((item, i) => (
                  <li key={i} className="flex items-start gap-3 font-body">
                    <svg
                      className="w-5 h-5 mt-0.5 flex-shrink-0"
                      fill="none"
                      stroke="#14A89E"
                      viewBox="0 0 24 24"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    <span className="text-sm text-slate-600">{item}</span>
                  </li>
                ))}
              </ul>

              <div className="flex items-center gap-6">
                <Link
                  to="/client/login"
                  className="inline-flex items-center px-6 py-3 text-sm font-semibold rounded-lg text-white transition-all font-body"
                  style={{
                    background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)',
                    boxShadow: '0 2px 12px rgba(20, 168, 158, 0.3)',
                  }}
                >
                  Access Your Portal
                </Link>
                <p className="text-sm text-slate-400 font-body">
                  Starting at <span className="font-semibold text-slate-600">$499</span>/month
                </p>
              </div>
            </div>

            {/* Visual — compliance status cards */}
            <div className="relative">
              <div className="space-y-4">
                <div className="p-6 rounded-2xl border border-slate-100 bg-white shadow-sm">
                  <div className="flex items-center justify-between mb-4">
                    <h4 className="text-sm font-semibold text-slate-900 font-body">Compliance Score</h4>
                    <span className="text-xs font-medium text-slate-300 italic font-body">Sample view</span>
                  </div>
                  <div className="flex items-end gap-3">
                    <span className="text-4xl font-bold tabular-nums font-body" style={{ color: '#0d9488' }}>96.4%</span>
                    <span className="text-sm text-green-600 mb-1 font-body">+2.1%</span>
                  </div>
                  <div className="mt-4 h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: '96.4%', background: 'linear-gradient(90deg, #14A89E, #0d9488)' }} />
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-4">
                  <div className="p-4 rounded-xl border border-slate-100 bg-white">
                    <p className="text-xs text-slate-400 mb-1 font-body">Passed</p>
                    <p className="text-2xl font-bold text-green-600 tabular-nums font-body">142</p>
                  </div>
                  <div className="p-4 rounded-xl border border-slate-100 bg-white">
                    <p className="text-xs text-slate-400 mb-1 font-body">Auto-healed</p>
                    <p className="text-2xl font-bold tabular-nums font-body" style={{ color: '#0d9488' }}>8</p>
                  </div>
                  <div className="p-4 rounded-xl border border-slate-100 bg-white">
                    <p className="text-xs text-slate-400 mb-1 font-body">Warnings</p>
                    <p className="text-2xl font-bold text-amber-500 tabular-nums font-body">3</p>
                  </div>
                </div>

                <div className="p-4 rounded-xl border border-green-100 bg-green-50/50">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 bg-green-500 rounded-full" />
                    <p className="text-sm font-medium text-green-800 font-body">
                      Firewall issue detected and remediated automatically — 2m ago
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <SectionDivider />

      {/* ═══════════════ FOR PARTNERS ═══════════════ */}
      <section id="partners" className="relative py-20 md:py-28 overflow-hidden">
        <div className="absolute inset-0 bg-slate-50/80" />
        <DotGrid className="opacity-15" />

        <div className="relative max-w-7xl mx-auto px-6">
          <div className="max-w-3xl mx-auto text-center mb-16">
            <p className="text-sm font-semibold uppercase tracking-widest mb-4 font-body" style={{ color: '#0d9488' }}>
              For MSP partners
            </p>
            <h2 className="font-display text-3xl md:text-4xl text-slate-900 mb-6">
              Compliance infrastructure you can offer — not build
            </h2>
            <p className="text-lg text-slate-500 leading-relaxed font-body font-light">
              White-label HIPAA compliance monitoring for your healthcare clients.
              Reduce manual audit preparation and repetitive remediation work.
              Serve an underserved market. Build recurring revenue on infrastructure
              that improves over time. Typical deployment: under 3 hours.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {[
              {
                icon: (
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                ),
                title: 'Fleet-wide visibility',
                desc: 'Single dashboard across all your healthcare clients. Site-level compliance scores, incident tracking, and healing status at a glance.',
              },
              {
                icon: (
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                ),
                title: 'Learning system',
                desc: 'Response patterns that work get promoted from advanced analysis to deterministic rules. Your infrastructure improves with each resolution.',
              },
              {
                icon: (
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" />
                  </svg>
                ),
                title: 'White-label ready',
                desc: 'Your brand, your client relationships. OsirisCare powers the infrastructure while your practice stays front and center.',
              },
              {
                icon: (
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                ),
                title: 'Automated reporting',
                desc: 'Compliance monitoring summaries generated from continuous observation. Evidence bundles with cryptographic integrity for each client site.',
              },
              {
                icon: (
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                ),
                title: 'Revenue opportunity',
                desc: 'Healthcare SMBs need compliance infrastructure and are willing to pay for it. Pricing from $499\u2013$1,299/month per practice depending on scope and healing depth.',
              },
              {
                icon: (
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                ),
                title: 'Operator-authorized',
                desc: 'Your team controls remediation policy. Routine issues are handled automatically by rules you approve. Critical actions always escalate for human decision.',
              },
            ].map((item, i) => (
              <div key={i} className="p-6 rounded-xl border border-slate-100 bg-white">
                <div
                  className="w-10 h-10 rounded-lg flex items-center justify-center mb-4 text-white"
                  style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
                >
                  {item.icon}
                </div>
                <h3 className="text-base font-semibold text-slate-900 mb-2 font-body">{item.title}</h3>
                <p className="text-sm text-slate-500 leading-relaxed font-body">{item.desc}</p>
              </div>
            ))}
          </div>

          <div className="text-center mt-12">
            <Link
              to="/partner/login"
              className="inline-flex items-center px-6 py-3 text-sm font-semibold rounded-lg text-white transition-all font-body"
              style={{
                background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)',
                boxShadow: '0 2px 12px rgba(20, 168, 158, 0.3)',
              }}
            >
              Partner Dashboard
              <svg className="w-4 h-4 ml-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
              </svg>
            </Link>
          </div>
        </div>
      </section>

      {/* ═══════════════ CONTACT / BOOK A DEMO ═══════════════ */}
      <section id="contact" className="relative py-20 md:py-28">
        <div className="max-w-7xl mx-auto px-6 text-center">
          <h2 className="font-display text-3xl md:text-4xl text-slate-900 mb-4">
            Ready to simplify compliance?
          </h2>
          <p className="text-lg text-slate-500 max-w-2xl mx-auto mb-10 font-body font-light">
            See how OsirisCare can monitor your practice in 30 minutes.
            No commitment, no sales pitch — just a walkthrough of what automated
            compliance monitoring looks like for your environment.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <a
              href="https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center px-8 py-4 text-base font-semibold rounded-lg text-white transition-all font-body"
              style={{
                background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)',
                boxShadow: '0 4px 24px rgba(20, 168, 158, 0.3)',
              }}
            >
              Schedule a Demo
              <svg className="w-5 h-5 ml-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
            </a>
            <a
              href="mailto:administrator@osiriscare.net"
              className="inline-flex items-center justify-center px-8 py-4 text-base font-semibold rounded-lg text-slate-700 border border-slate-200 hover:border-slate-300 hover:bg-slate-50 transition-all font-body"
            >
              Contact Us
              <svg className="w-5 h-5 ml-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </a>
          </div>
        </div>
      </section>

      {/* ═══════════════ FOOTER ═══════════════ */}
      <footer className="border-t border-slate-100 bg-white">
        <div className="max-w-7xl mx-auto px-6 py-16">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-12 mb-12">
            {/* Brand */}
            <div className="md:col-span-2">
              <div className="flex items-center gap-3 mb-4">
                <div
                  className="w-8 h-8 rounded-lg flex items-center justify-center"
                  style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
                >
                  <OsirisCareLeaf className="w-4 h-4" color="white" />
                </div>
                <span className="text-base font-semibold text-slate-900 font-body">OsirisCare</span>
              </div>
              <p className="text-sm text-slate-400 leading-relaxed max-w-sm font-body">
                Always audit-ready — with proof, not paperwork. Continuous monitoring,
                verifiable evidence, and operator-authorized remediation
                designed to support HIPAA compliance programs.
              </p>
            </div>

            {/* Links */}
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-4 font-body">Platform</h4>
              <ul className="space-y-2.5">
                <li><Link to="/pricing" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">Pricing</Link></li>
                <li><Link to="/for-msps" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">For MSPs</Link></li>
                <li><Link to="/signup" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">Start Pilot</Link></li>
                <li><a href="#how-it-works" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">How It Works</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-4 font-body">Resources</h4>
              <ul className="space-y-2.5">
                <li><Link to="/2026-hipaa-update" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">2026 HIPAA Rule</Link></li>
                <li><Link to="/blog" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">Blog</Link></li>
                <li><Link to="/compare/vanta" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">vs Vanta</Link></li>
                <li><Link to="/compare/drata" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">vs Drata</Link></li>
                <li><Link to="/changelog" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">Changelog</Link></li>
                <li><Link to="/legal/privacy" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">Privacy</Link></li>
                <li><Link to="/legal/baa" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">BAA</Link></li>
              </ul>
            </div>
          </div>

          {/* Legal disclaimer */}
          <div className="border-t border-slate-100 pt-8">
            <p className="text-xs text-slate-400 leading-relaxed max-w-4xl font-body">
              {DISCLAIMERS.landing_legal}
            </p>
            <p className="text-xs text-slate-300 mt-4 font-body">
              &copy; {new Date().getFullYear()} {BRANDING.name}. All rights reserved.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;

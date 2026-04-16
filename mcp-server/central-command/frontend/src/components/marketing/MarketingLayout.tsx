import React from 'react';
import { Link } from 'react-router-dom';
import { OsirisCareLeaf } from '../shared';

/**
 * MarketingLayout — shared nav + footer for the public content funnel.
 *
 * Pages that live on www.osiriscare.net (2026 HIPAA page, comparison
 * pages, blog posts, /for-msps) use this layout so visual + nav
 * consistency doesn't decay per page.
 *
 * LandingPage.tsx and RecoveryLanding.tsx predate this layout and each
 * ship their own nav/footer; don't refactor them into this component
 * without deliberate design review — they each have hero treatments
 * that a shared shell would dilute.
 */
export const MarketingLayout: React.FC<{
  children: React.ReactNode;
  activeNav?: string;
  canonicalPath?: string;
}> = ({ children, activeNav }) => {
  return (
    <div
      className="min-h-screen bg-white"
      style={{ fontFamily: "'DM Sans', 'Helvetica Neue', system-ui, sans-serif" }}
    >
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Serif+Display&display=swap');
        .font-display { font-family: 'DM Serif Display', Georgia, serif; }
        .font-body { font-family: 'DM Sans', 'Helvetica Neue', system-ui, sans-serif; }
      `}</style>

      <nav className="sticky top-0 z-50 border-b border-slate-100 bg-white/95 backdrop-blur">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3">
            <div
              className="w-9 h-9 rounded-lg flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              <OsirisCareLeaf className="w-5 h-5" color="white" />
            </div>
            <span className="text-lg font-semibold text-slate-900 tracking-tight font-body">
              OsirisCare
            </span>
          </Link>

          <div className="hidden md:flex items-center gap-8 text-sm font-medium text-slate-500 font-body">
            <NavLink to="/2026-hipaa-update" active={activeNav === '2026'}>
              2026 HIPAA Rule
            </NavLink>
            <NavLink to="/for-msps" active={activeNav === 'msps'}>
              For MSPs
            </NavLink>
            <NavLink to="/pricing" active={activeNav === 'pricing'}>
              Pricing
            </NavLink>
            <NavLink to="/blog" active={activeNav === 'blog'}>
              Blog
            </NavLink>
            <NavLink to="/changelog" active={activeNav === 'changelog'}>
              Changelog
            </NavLink>
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
              to="/signup"
              className="text-sm font-medium px-4 py-2 rounded-lg text-white transition-all font-body"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              Start Pilot
            </Link>
          </div>
        </div>

        {/* 2026-ready badge — persistent, quiet */}
        <div className="bg-teal-50 border-t border-teal-100">
          <div className="max-w-7xl mx-auto px-6 py-2 text-center">
            <span className="text-xs text-teal-800 font-body">
              <span className="inline-block w-2 h-2 rounded-full bg-teal-600 mr-2 align-middle" />
              <strong>2026-ready:</strong> platform built for the HHS Security Rule NPRM
              (Dec 2024) — MFA, encryption, asset inventory, vulnerability scanning all monitored today.{' '}
              <Link to="/2026-hipaa-update" className="underline hover:text-teal-900 font-semibold">
                See the 9 new requirements →
              </Link>
            </span>
          </div>
        </div>
      </nav>

      <main>{children}</main>

      <footer className="border-t border-slate-100 bg-white mt-24">
        <div className="max-w-7xl mx-auto px-6 py-14">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-8 mb-10">
            <div className="col-span-2">
              <div className="flex items-center gap-3 mb-3">
                <div
                  className="w-8 h-8 rounded-lg flex items-center justify-center"
                  style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
                >
                  <OsirisCareLeaf className="w-4 h-4" color="white" />
                </div>
                <span className="text-base font-semibold text-slate-900 font-body">OsirisCare</span>
              </div>
              <p className="text-sm text-slate-500 leading-relaxed max-w-sm font-body">
                HIPAA compliance attestation platform for 1–50 provider healthcare
                practices. Continuous monitoring, cryptographically verifiable
                evidence, operator-authorized remediation.
              </p>
            </div>

            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3 font-body">Platform</h4>
              <ul className="space-y-2 text-sm font-body">
                <li><Link to="/" className="text-slate-500 hover:text-slate-900">How It Works</Link></li>
                <li><Link to="/pricing" className="text-slate-500 hover:text-slate-900">Pricing</Link></li>
                <li><Link to="/for-msps" className="text-slate-500 hover:text-slate-900">For MSPs</Link></li>
                <li><Link to="/signup" className="text-slate-500 hover:text-slate-900">Start Pilot</Link></li>
              </ul>
            </div>

            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3 font-body">Compare</h4>
              <ul className="space-y-2 text-sm font-body">
                <li><Link to="/compare/vanta" className="text-slate-500 hover:text-slate-900">vs Vanta</Link></li>
                <li><Link to="/compare/drata" className="text-slate-500 hover:text-slate-900">vs Drata</Link></li>
                <li><Link to="/compare/delve" className="text-slate-500 hover:text-slate-900">vs Delve</Link></li>
              </ul>
            </div>

            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3 font-body">Resources</h4>
              <ul className="space-y-2 text-sm font-body">
                <li><Link to="/2026-hipaa-update" className="text-slate-500 hover:text-slate-900">2026 HIPAA Rule</Link></li>
                <li><Link to="/blog" className="text-slate-500 hover:text-slate-900">Blog</Link></li>
                <li><Link to="/changelog" className="text-slate-500 hover:text-slate-900">Changelog</Link></li>
                <li><Link to="/recovery" className="text-slate-500 hover:text-slate-900">Migrating from another vendor</Link></li>
                <li><Link to="/legal/privacy" className="text-slate-500 hover:text-slate-900">Privacy</Link></li>
                <li><Link to="/legal/baa" className="text-slate-500 hover:text-slate-900">BAA</Link></li>
              </ul>
            </div>
          </div>

          <div className="border-t border-slate-100 pt-6 text-xs text-slate-400 font-body leading-relaxed">
            OsirisCare provides compliance monitoring, evidence capture, and
            human-authorized remediation workflows. It is not a substitute for
            legal counsel or a designated HIPAA Security Officer. Claims about
            the 2026 HIPAA Security Rule NPRM reference the Notice of Proposed
            Rulemaking (HHS-OCR-0945-AA22) published in the Federal Register
            on December 27, 2024; final rule provisions are subject to change
            during rulemaking.
            <div className="mt-3 text-slate-300">
              &copy; {new Date().getFullYear()} OsirisCare. All rights reserved.
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
};

const NavLink: React.FC<{ to: string; active?: boolean; children: React.ReactNode }> = ({ to, active, children }) => (
  <Link
    to={to}
    className={`transition-colors ${active ? 'text-slate-900 font-semibold' : 'hover:text-slate-900'}`}
  >
    {children}
  </Link>
);

export default MarketingLayout;

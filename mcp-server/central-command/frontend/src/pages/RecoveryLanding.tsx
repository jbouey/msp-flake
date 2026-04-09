import React from 'react';
import { Link } from 'react-router-dom';

/**
 * RecoveryLanding — public landing page for compliance refugees.
 *
 * Built in response to the April 2026 Delve / DeepDelver scandal in
 * which a Y-Combinator–backed compliance automation startup was accused
 * of fabricating audit evidence and generating identical-boilerplate
 * reports across hundreds of clients. This page is for healthcare
 * practices whose prior compliance vendor just lost their trust and
 * need to migrate to a platform whose evidence will survive an
 * auditor walking in tomorrow with skepticism and a copy of the
 * DeepDelver Substack.
 *
 * The page deliberately AVOIDS naming Delve directly — the goal is
 * "switch from your fraudulent vendor", not a competitive smear. The
 * sales argument is positive: cryptographic proof, browser-side
 * verification, public security advisories, downloadable auditor kit.
 *
 * Lives at /recovery on the public landing site (www.osiriscare.net).
 */
export const RecoveryLanding: React.FC = () => {
  return (
    <div
      className="min-h-screen bg-white"
      style={{ fontFamily: "'DM Sans', 'Helvetica Neue', system-ui, sans-serif" }}
    >
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Serif+Display&display=swap');
        .font-display { font-family: 'DM Serif Display', Georgia, serif; }
      `}</style>

      {/* Header */}
      <header className="border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-5 flex items-center justify-between">
          <Link to="/" className="font-display text-xl font-semibold text-slate-900">
            OsirisCare
          </Link>
          <nav className="flex items-center gap-6 text-sm">
            <Link to="/pricing" className="text-slate-600 hover:text-teal-700">
              Pricing
            </Link>
            <a
              href="mailto:support@osiriscare.net"
              className="text-slate-600 hover:text-teal-700"
            >
              Contact
            </a>
            <a
              href="https://api.osiriscare.net/dashboard"
              className="text-sm font-medium text-white bg-teal-700 hover:bg-teal-800 rounded-md px-4 py-2"
            >
              Sign in
            </a>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section className="border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-16 lg:py-24">
          <div className="max-w-3xl">
            <p className="text-xs uppercase tracking-[0.2em] text-teal-700 font-medium mb-4">
              For practices migrating from a compromised compliance vendor
            </p>
            <h1 className="font-display text-4xl lg:text-6xl text-slate-900 leading-tight mb-6">
              Real evidence. Cryptographically provable.{' '}
              <span className="text-teal-700">Verified on your auditor's laptop.</span>
            </h1>
            <p className="text-lg text-slate-600 leading-relaxed mb-8">
              Recent events in the compliance-automation market have left
              healthcare practices wondering whether their audit reports are
              worth the PDFs they were printed on. If your prior vendor just
              lost your trust, OsirisCare is built so you don't have to
              trust us either — every claim we make is independently
              verifiable using open-source tools, with no platform
              dependency.
            </p>
            <div className="flex items-center gap-4">
              <a
                href="mailto:recovery@osiriscare.net?subject=Migration%20from%20compliance%20vendor"
                className="inline-flex items-center gap-2 px-6 py-3 bg-teal-700 hover:bg-teal-800 text-white font-medium rounded-md"
              >
                Start migration conversation →
              </a>
              <Link
                to="/pricing"
                className="inline-flex items-center gap-2 px-6 py-3 border border-slate-300 hover:bg-slate-50 text-slate-700 font-medium rounded-md"
              >
                See pricing
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Three pillars */}
      <section className="border-b border-slate-200 bg-slate-50">
        <div className="max-w-6xl mx-auto px-6 py-16">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div>
              <div className="w-12 h-12 bg-teal-100 rounded-lg flex items-center justify-center mb-4">
                <svg className="w-6 h-6 text-teal-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
              <h3 className="font-display text-2xl text-slate-900 mb-3">Verifiable, not asserted</h3>
              <p className="text-slate-600 leading-relaxed">
                Every compliance check is collected by an on-prem appliance,
                Ed25519-signed by a per-appliance key, hash-chained to its
                predecessor, then anchored to the Bitcoin blockchain via
                OpenTimestamps. Your auditor can verify each claim using
                <code className="px-1 mx-1 text-xs bg-slate-100 rounded">sha256sum</code>
                +
                <code className="px-1 mx-1 text-xs bg-slate-100 rounded">ots verify</code>
                — no OsirisCare network access required.
              </p>
            </div>
            <div>
              <div className="w-12 h-12 bg-teal-100 rounded-lg flex items-center justify-center mb-4">
                <svg className="w-6 h-6 text-teal-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 17l6-6 4 4 8-8m0 0V9m0-2h-2" />
                </svg>
              </div>
              <h3 className="font-display text-2xl text-slate-900 mb-3">Browser-verified, in real time</h3>
              <p className="text-slate-600 leading-relaxed">
                When you (or your auditor) view the OsirisCare scorecard,
                the page itself runs Ed25519 signature verification and
                hash-chain checks{' '}
                <strong>in your browser</strong>, against your pinned per-
                appliance public keys. The "verified" badge is computed
                locally — there is no server-trusted "looks-good" flag to
                fake. You can watch the verifications fire in your browser's
                developer tools.
              </p>
            </div>
            <div>
              <div className="w-12 h-12 bg-teal-100 rounded-lg flex items-center justify-center mb-4">
                <svg className="w-6 h-6 text-teal-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <h3 className="font-display text-2xl text-slate-900 mb-3">Public when we find a bug</h3>
              <p className="text-slate-600 leading-relaxed">
                On April 9, 2026 we found a Merkle batch_id collision in our
                own evidence aggregator that left 1,198 bundles with
                unverifiable proofs. We remediated the writer, backfilled
                the affected bundles to a clearly-labeled <code className="px-1 text-xs bg-slate-100 rounded">legacy</code> state,
                and{' '}
                <a
                  className="text-teal-700 hover:underline font-medium"
                  href="/security/advisories/2026-04-09-merkle"
                >
                  published a public advisory
                </a>
                {' '}the same day. We do this every time. If your prior
                vendor has not published a similar advisory in response to
                recent industry events, that absence is information.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* The auditor kit */}
      <section className="border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-20">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
            <div>
              <p className="text-xs uppercase tracking-[0.2em] text-teal-700 font-medium mb-3">
                The auditor handoff
              </p>
              <h2 className="font-display text-4xl text-slate-900 mb-6 leading-tight">
                Hand your auditor one ZIP. Watch them verify it in 5 minutes.
              </h2>
              <p className="text-slate-600 leading-relaxed mb-4">
                Click one button in the OsirisCare portal and download a
                self-contained verification kit. The ZIP includes every
                evidence bundle for your site, every per-appliance public
                key, every OpenTimestamps proof file, and a{' '}
                <code className="px-1 text-xs bg-slate-100 rounded">verify.sh</code>
                {' '}script that walks the verification end-to-end.
              </p>
              <p className="text-slate-600 leading-relaxed mb-6">
                Your auditor opens a terminal, runs{' '}
                <code className="px-1 text-xs bg-slate-100 rounded">bash verify.sh</code>,
                and gets a clean PASS/FAIL report against the Bitcoin
                blockchain. No platform login required. No vendor
                cooperation required. No trust required.
              </p>
              <ul className="space-y-2 text-sm text-slate-600">
                <li className="flex items-start gap-2">
                  <span className="text-teal-700 font-bold">✓</span>
                  Bundle-level SHA-256 + Ed25519 signature verification
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-teal-700 font-bold">✓</span>
                  Hash-chain linkage walk
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-teal-700 font-bold">✓</span>
                  Per-bundle{' '}
                  <code className="px-1 text-xs bg-slate-100 rounded">.ots</code>{' '}
                  files for offline OpenTimestamps verification
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-teal-700 font-bold">✓</span>
                  Public-key fingerprints for offline pinning
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-teal-700 font-bold">✓</span>
                  Documented disclosures inline (auditor sees the same
                  remediation log we publish)
                </li>
              </ul>
            </div>
            <div className="bg-slate-900 rounded-lg p-6 text-slate-100 font-mono text-sm overflow-x-auto">
              <div className="text-slate-500 mb-2">$ bash verify.sh</div>
              <div className="text-slate-100">Bundles in kit: 142</div>
              <div className="text-emerald-400">[PASS] hash chain     141/141 links verified</div>
              <div className="text-emerald-400">[PASS] signatures     142/142 verified against pinned pubkeys</div>
              <div className="text-slate-400">[INFO] ots proofs      89  bundles anchored in Bitcoin</div>
              <div className="text-slate-400">[INFO] legacy bundles  53  (pre-anchoring or documented reclassification)</div>
              <div className="mt-2">&nbsp;</div>
              <div className="text-emerald-400 font-bold">VERIFICATION PASSED</div>
            </div>
          </div>
        </div>
      </section>

      {/* Migration */}
      <section className="border-b border-slate-200 bg-slate-50">
        <div className="max-w-6xl mx-auto px-6 py-20">
          <div className="max-w-3xl">
            <p className="text-xs uppercase tracking-[0.2em] text-teal-700 font-medium mb-3">
              Migration plan
            </p>
            <h2 className="font-display text-4xl text-slate-900 mb-6 leading-tight">
              You don't have to restart your audit clock.
            </h2>
            <p className="text-slate-600 leading-relaxed mb-8">
              We will accept your prior compliance evidence as imported
              legacy artifacts (clearly labeled as such in your audit
              trail) so your six-year HIPAA retention runs uninterrupted.
              From the moment your appliance phones home, you start
              accumulating real cryptographically-anchored evidence under
              OsirisCare while your historical record stays visible to
              auditors.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="bg-white rounded-lg border border-slate-200 p-5">
                <div className="text-xs font-semibold text-teal-700 mb-2">STEP 1 — DAY 1</div>
                <h3 className="font-medium text-slate-900 mb-2">Migration BAA</h3>
                <p className="text-sm text-slate-600">
                  Sign our migration-specific BAA that explicitly handles
                  the cutover period, then ship a Golden Flake appliance
                  to your office.
                </p>
              </div>
              <div className="bg-white rounded-lg border border-slate-200 p-5">
                <div className="text-xs font-semibold text-teal-700 mb-2">STEP 2 — WEEK 1</div>
                <h3 className="font-medium text-slate-900 mb-2">Import + observe</h3>
                <p className="text-sm text-slate-600">
                  Bulk-upload your prior evidence as imported legacy
                  artifacts. Plug in the appliance — no further action
                  required. It begins hash-chaining and anchoring from
                  the moment it phones home.
                </p>
              </div>
              <div className="bg-white rounded-lg border border-slate-200 p-5">
                <div className="text-xs font-semibold text-teal-700 mb-2">STEP 3 — ONGOING</div>
                <h3 className="font-medium text-slate-900 mb-2">Monthly compliance packets</h3>
                <p className="text-sm text-slate-600">
                  Auto-generated packets persist in our database for the
                  full HIPAA six-year retention window. Download yours,
                  hand it to your auditor, watch them verify it locally.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Honest comparison */}
      <section className="border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-20">
          <p className="text-xs uppercase tracking-[0.2em] text-teal-700 font-medium mb-3">
            How we differ from compliance-automation tools
          </p>
          <h2 className="font-display text-4xl text-slate-900 mb-10 leading-tight">
            Speed is not the product. Evidence integrity is.
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="text-left py-3 text-sm font-medium text-slate-500 uppercase tracking-wide">What customers usually want</th>
                  <th className="text-left py-3 text-sm font-medium text-slate-500 uppercase tracking-wide">What automation tools deliver</th>
                  <th className="text-left py-3 text-sm font-medium text-slate-500 uppercase tracking-wide">What OsirisCare does</th>
                </tr>
              </thead>
              <tbody className="text-sm">
                <tr className="border-b border-slate-100">
                  <td className="py-4 pr-4 text-slate-700">Audit-ready in days, not months</td>
                  <td className="py-4 pr-4 text-slate-500">Pre-generated boilerplate evidence</td>
                  <td className="py-4 text-slate-900">Real evidence collected by an on-prem appliance from the moment it phones home</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-4 pr-4 text-slate-700">A clean dashboard with green check-marks</td>
                  <td className="py-4 pr-4 text-slate-500">Server-attested "Compliant" badges</td>
                  <td className="py-4 text-slate-900">Browser-side cryptographic verification, computed locally</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-4 pr-4 text-slate-700">A trust page to put on the website</td>
                  <td className="py-4 pr-4 text-slate-500">Trust page published before any compliance work</td>
                  <td className="py-4 text-slate-900">Trust page reflects the implemented appliance scans, dated and signed</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-4 pr-4 text-slate-700">An auditor signs off quickly</td>
                  <td className="py-4 pr-4 text-slate-500">Auditor receives templated reports and rubber-stamps</td>
                  <td className="py-4 text-slate-900">Auditor downloads a ZIP, runs a script, verifies against the Bitcoin blockchain locally</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-4 pr-4 text-slate-700">A vendor that owns its claims</td>
                  <td className="py-4 pr-4 text-slate-500">Liability shifted to a third-party auditor</td>
                  <td className="py-4 text-slate-900">We sign every compliance packet with our own Ed25519 root key. We own the claim.</td>
                </tr>
                <tr>
                  <td className="py-4 pr-4 text-slate-700">A vendor that tells you when something breaks</td>
                  <td className="py-4 pr-4 text-slate-500">Silent denial, anonymous accusations, eventual scandal</td>
                  <td className="py-4 text-slate-900">Public security advisories the same day we find a bug. <a className="text-teal-700 hover:underline" href="/security/advisories/2026-04-09-merkle">Read our most recent one.</a></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Footer CTA */}
      <section className="bg-slate-900 text-white">
        <div className="max-w-6xl mx-auto px-6 py-20 text-center">
          <h2 className="font-display text-4xl mb-4 leading-tight">
            Switch to a compliance platform that earns its name.
          </h2>
          <p className="text-slate-300 max-w-2xl mx-auto mb-8">
            We will help you migrate without restarting your audit clock,
            preserve your historical evidence, and walk into your next
            audit with a single ZIP your auditor can verify on the spot.
          </p>
          <a
            href="mailto:recovery@osiriscare.net?subject=Migration%20from%20compliance%20vendor"
            className="inline-flex items-center gap-2 px-8 py-4 bg-teal-500 hover:bg-teal-400 text-slate-900 font-medium rounded-md"
          >
            Start migration conversation →
          </a>
          <p className="mt-6 text-xs text-slate-400">
            Or email{' '}
            <a className="underline hover:text-white" href="mailto:recovery@osiriscare.net">
              recovery@osiriscare.net
            </a>
          </p>
        </div>
      </section>

      {/* Compact footer */}
      <footer className="border-t border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-8 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div className="text-sm text-slate-500">
            © 2026 OsirisCare · HIPAA compliance attestation substrate ·
            Evidence anchored to Bitcoin via OpenTimestamps
          </div>
          <div className="flex items-center gap-6 text-sm">
            <Link to="/legal/terms" className="text-slate-600 hover:text-teal-700">
              Terms
            </Link>
            <Link to="/legal/privacy" className="text-slate-600 hover:text-teal-700">
              Privacy
            </Link>
            <a
              className="text-slate-600 hover:text-teal-700"
              href="/security/advisories/2026-04-09-merkle"
            >
              Security advisories
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default RecoveryLanding;

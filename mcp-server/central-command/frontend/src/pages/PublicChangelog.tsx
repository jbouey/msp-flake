import React from 'react';

/**
 * Public changelog — Session 203 Tier 2.6.
 *
 * The Delve / DeepDelver scandal has put the entire compliance-automation
 * category on notice. Trust now requires public, dated, customer-visible
 * evidence that the platform actually changes — not just opaque "version
 * updates" that nobody can verify.
 *
 * This page is the first iteration of that public log. Each entry is a
 * customer-relevant change with a date, a one-line summary, and a link to
 * supporting documentation when applicable. Engineering work that has no
 * customer impact is omitted intentionally — this is not a `git log`.
 *
 * Future iterations will add:
 *   - RSS feed
 *   - Filter by category (security / feature / fix)
 *   - Email subscription
 *
 * Editing: add new entries at the TOP of the `ENTRIES` array. Keep
 * summaries truthful and conservative; this page is read by auditors.
 */

interface ChangelogEntry {
  date: string; // ISO date — YYYY-MM-DD
  category: 'security' | 'feature' | 'fix' | 'disclosure';
  title: string;
  summary: string;
  /** Optional supporting link — internal docs, security advisory, RFC. */
  link?: { label: string; href: string };
}

const ENTRIES: ChangelogEntry[] = [
  {
    date: '2026-04-09',
    category: 'feature',
    title: 'Full-chain browser verification',
    summary:
      'PortalVerify now offers a "Verify entire chain" button that walks every evidence bundle in a Web Worker on the auditor\'s own device, runs Ed25519 signature and SHA-256 hash-chain verification locally, and reports incremental progress. No backend trust required.',
  },
  {
    date: '2026-04-09',
    category: 'feature',
    title: 'Auditor verification kit ZIP download',
    summary:
      'Every site now exposes a downloadable auditor kit at /api/evidence/sites/{site_id}/auditor-kit. The ZIP contains the README, a verify.sh script, the chain manifest, every Ed25519 public key, and every OpenTimestamps proof. The verifier makes zero network calls back to OsirisCare.',
  },
  {
    date: '2026-04-09',
    category: 'disclosure',
    title: 'Security advisory: Merkle batch_id collision',
    summary:
      'OSIRIS-2026-04-09-MERKLE-COLLISION. We discovered a bug in the Merkle batch ID generator that produced 1,198 evidence bundles whose stored Merkle proofs could not verify against the anchored Bitcoin root. Remediated, backfilled to legacy state, and disclosed publicly the same day.',
    link: {
      label: 'Read advisory',
      href: '/docs/security/SECURITY_ADVISORY_2026-04-09_MERKLE.md',
    },
  },
  {
    date: '2026-04-09',
    category: 'feature',
    title: 'HIPAA disclosure accounting view (§164.528)',
    summary:
      'Client portal users can now view their org\'s full audit log at /client/audit-log: who did what, when, and from which IP address. 13 mutating actions across user management, MFA, credentials, drift config, devices, and alerts are recorded with append-only protection at the database level.',
  },
  {
    date: '2026-04-09',
    category: 'fix',
    title: 'Compliance packet cron resilience',
    summary:
      'The monthly compliance packet generator was previously gated on a one-hour window per month. We removed the gate, made the loop walk the last three months, added Redis-based locking, and made the writes idempotent. HIPAA §164.316(b)(2)(i) requires 6-year retention — the gap is now closed.',
  },
  {
    date: '2026-04-09',
    category: 'security',
    title: 'Partner MFA → Redis + portal rate limiting',
    summary:
      'Partner MFA pending tokens moved from in-memory dict to Redis with TTL. Client portal magic-link and login endpoints are now rate limited (5 attempts → 15-minute lockout) with the same backoff used by the admin portal.',
  },
  {
    date: '2026-04-08',
    category: 'feature',
    title: 'Per-appliance Ed25519 keys + browser-verified signatures',
    summary:
      'Multi-appliance sites no longer share a single Ed25519 signing key — each appliance has its own. The portal now exposes a public-keys endpoint and the browser verifies signatures locally using @noble/ed25519, replacing the server\'s self-reported "valid" badge with a browser-computed one.',
  },
  {
    date: '2026-04-07',
    category: 'feature',
    title: 'Site Detail enterprise polish',
    summary:
      'SiteDetail page rebuilt: hero compliance card, deployment progress, audit trail, activity timeline, decommission triple-guard, SLA strip, search bar, floating action button, and a phase-2 refactor that split a 2045-line file into 11 sub-components.',
  },
  {
    date: '2026-04-05',
    category: 'security',
    title: 'Credential encryption key rotation',
    summary:
      'All site credentials are now encrypted with a Fernet key that can be rotated without downtime via MultiFernet. New admin endpoint and background re-encrypt worker. Documented in KEY_ROTATION_RUNBOOK.md.',
  },
];

const CATEGORY_STYLES: Record<ChangelogEntry['category'], { label: string; className: string }> = {
  security: { label: 'Security', className: 'bg-red-100 text-red-800 border-red-200' },
  feature: { label: 'Feature', className: 'bg-blue-100 text-blue-800 border-blue-200' },
  fix: { label: 'Fix', className: 'bg-amber-100 text-amber-800 border-amber-200' },
  disclosure: {
    label: 'Disclosure',
    className: 'bg-purple-100 text-purple-800 border-purple-200',
  },
};

const formatDate = (iso: string) => {
  const d = new Date(iso + 'T00:00:00Z');
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
};

export const PublicChangelog: React.FC = () => {
  // Group entries by month so the page reads like a real changelog
  const grouped = ENTRIES.reduce<Record<string, ChangelogEntry[]>>((acc, e) => {
    const month = e.date.slice(0, 7); // YYYY-MM
    (acc[month] ||= []).push(e);
    return acc;
  }, {});

  const months = Object.keys(grouped).sort().reverse();

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-white border-b border-slate-200 py-12">
        <div className="max-w-3xl mx-auto px-6">
          <p className="text-xs uppercase tracking-widest text-slate-500 mb-2">
            OsirisCare changelog
          </p>
          <h1 className="text-4xl font-bold text-slate-900 mb-3">
            What we ship, in public
          </h1>
          <p className="text-slate-600 leading-relaxed max-w-2xl">
            Every customer-relevant change with a date, a one-line summary, and a
            link to supporting documentation when applicable. We publish this
            page because compliance customers should be able to verify that
            the platform actually changes — not just trust our roadmap deck.
          </p>
          <div className="mt-6 flex flex-wrap gap-3 text-xs">
            {Object.entries(CATEGORY_STYLES).map(([key, style]) => (
              <span
                key={key}
                className={`inline-block px-2.5 py-1 rounded-full border ${style.className}`}
              >
                {style.label}
              </span>
            ))}
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-12">
        {months.map((month) => (
          <section key={month} className="mb-12">
            <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-4 sticky top-0 bg-slate-50 py-2 -mx-6 px-6">
              {new Date(month + '-01T00:00:00Z').toLocaleDateString(undefined, {
                year: 'numeric',
                month: 'long',
              })}
            </h2>
            <div className="space-y-6">
              {grouped[month].map((entry, i) => {
                const style = CATEGORY_STYLES[entry.category];
                return (
                  <article
                    key={`${entry.date}-${i}`}
                    className="bg-white rounded-xl border border-slate-200 p-6 hover:shadow-sm transition"
                  >
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <div className="flex items-center gap-3">
                        <span
                          className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border ${style.className}`}
                        >
                          {style.label}
                        </span>
                        <time className="text-xs text-slate-500">
                          {formatDate(entry.date)}
                        </time>
                      </div>
                    </div>
                    <h3 className="text-lg font-semibold text-slate-900 mb-2">
                      {entry.title}
                    </h3>
                    <p className="text-sm text-slate-700 leading-relaxed">
                      {entry.summary}
                    </p>
                    {entry.link && (
                      <a
                        href={entry.link.href}
                        className="mt-3 inline-flex items-center text-xs font-medium text-blue-700 hover:text-blue-900"
                      >
                        {entry.link.label}
                        <span className="ml-1">→</span>
                      </a>
                    )}
                  </article>
                );
              })}
            </div>
          </section>
        ))}

        <footer className="mt-16 pt-8 border-t border-slate-200 text-xs text-slate-500">
          <p className="mb-2">
            <strong>Editing this page:</strong> add new entries at the TOP of the
            ENTRIES array in <code>src/pages/PublicChangelog.tsx</code>. Keep
            summaries truthful and conservative — auditors read this page.
          </p>
          <p>
            For security-relevant changes, link to the corresponding advisory in{' '}
            <code>docs/security/</code>. For evidence-integrity events, publish
            the advisory the same day you remediate.
          </p>
        </footer>
      </main>
    </div>
  );
};

export default PublicChangelog;

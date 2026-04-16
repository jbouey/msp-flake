import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { MarketingLayout } from './MarketingLayout';
import { JsonLd } from './JsonLd';

/**
 * ComparePage — reusable comparison-page scaffold for /compare/* routes.
 *
 * Takes competitor name, positioning summary, and a comparison matrix.
 * The comparison matrix is deliberately framed around capabilities the
 * prospect cares about, not feel-good-for-us bullets. Honest framing
 * stays out of the gray zone of smear: each competitor gets an
 * accurate "where they shine" column.
 */

export interface CompareRow {
  dimension: string;
  osiris: string;
  competitor: string;
  winner?: 'osiris' | 'competitor' | 'tie';
}

export interface ComparePageProps {
  competitorName: string;
  canonicalSlug: string; // e.g. "vanta", "drata", "delve"
  tagline: string;
  theirStrengths: string[];
  rows: CompareRow[];
  whoShouldPickUs: string;
  whoShouldPickThem: string;
  narrativeIntro: string;
}

export const ComparePage: React.FC<ComparePageProps> = ({
  competitorName,
  canonicalSlug,
  tagline,
  theirStrengths,
  rows,
  whoShouldPickUs,
  whoShouldPickThem,
  narrativeIntro,
}) => {
  useEffect(() => {
    document.title = `OsirisCare vs ${competitorName} — HIPAA Compliance Comparison | OsirisCare`;
    setCanonicalAndDescription(
      `https://www.osiriscare.net/compare/${canonicalSlug}`,
      `OsirisCare vs ${competitorName} — honest comparison for healthcare practices and multi-site provider networks evaluating HIPAA compliance platforms. Where each shines, where they diverge, and who should pick which.`,
    );
  }, [competitorName, canonicalSlug]);

  return (
    <MarketingLayout>
      <JsonLd
        data={{
          '@context': 'https://schema.org',
          '@type': 'WebPage',
          name: `OsirisCare vs ${competitorName}`,
          description: `Honest comparison of OsirisCare and ${competitorName} for healthcare HIPAA compliance.`,
          url: `https://www.osiriscare.net/compare/${canonicalSlug}`,
          isPartOf: { '@type': 'WebSite', name: 'OsirisCare', url: 'https://www.osiriscare.net' },
        }}
      />

      <section className="border-b border-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-16 lg:py-24">
          <p className="text-xs uppercase tracking-[0.2em] text-teal-700 font-semibold mb-4">
            Comparison · Updated April 2026
          </p>
          <h1 className="font-display text-4xl lg:text-5xl text-slate-900 leading-tight mb-6">
            OsirisCare vs {competitorName}
          </h1>
          <p className="text-xl text-slate-600 font-body mb-6">{tagline}</p>
          <p className="text-base text-slate-600 leading-relaxed font-body">{narrativeIntro}</p>
        </div>
      </section>

      {/* Their strengths — honest framing */}
      <section className="bg-slate-50 border-b border-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-12">
          <h2 className="font-display text-2xl text-slate-900 mb-4">
            Where {competitorName} shines
          </h2>
          <ul className="space-y-2 text-slate-700 font-body">
            {theirStrengths.map((s, i) => (
              <li key={i} className="flex gap-3">
                <span className="text-teal-600 flex-shrink-0 mt-1">•</span>
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Comparison matrix */}
      <section>
        <div className="max-w-5xl mx-auto px-6 py-16">
          <h2 className="font-display text-3xl text-slate-900 mb-10">Head to head</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse font-body">
              <thead>
                <tr className="border-b border-slate-200 text-left">
                  <th className="py-3 pr-4 text-slate-500 font-semibold w-1/4">Dimension</th>
                  <th className="py-3 pr-4 text-teal-700 font-semibold">OsirisCare</th>
                  <th className="py-3 pr-4 text-slate-700 font-semibold">{competitorName}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.dimension} className="border-b border-slate-100 align-top">
                    <td className="py-4 pr-4 text-slate-900 font-medium">{row.dimension}</td>
                    <td className={`py-4 pr-4 leading-relaxed ${row.winner === 'osiris' ? 'text-teal-800' : 'text-slate-700'}`}>
                      {row.osiris}
                    </td>
                    <td className={`py-4 pr-4 leading-relaxed ${row.winner === 'competitor' ? 'text-slate-900' : 'text-slate-600'}`}>
                      {row.competitor}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Decision helper */}
      <section className="bg-slate-50 border-t border-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-16">
          <h2 className="font-display text-3xl text-slate-900 mb-8">Which should you pick?</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="bg-white rounded-xl border border-teal-200 p-6">
              <h3 className="font-display text-xl text-teal-800 mb-3">Pick OsirisCare if</h3>
              <p className="text-slate-700 leading-relaxed font-body">{whoShouldPickUs}</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-6">
              <h3 className="font-display text-xl text-slate-800 mb-3">Pick {competitorName} if</h3>
              <p className="text-slate-700 leading-relaxed font-body">{whoShouldPickThem}</p>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section>
        <div className="max-w-4xl mx-auto px-6 py-20 text-center">
          <h2 className="font-display text-3xl text-slate-900 mb-6">Want to see it for yourself?</h2>
          <div className="flex items-center justify-center gap-4">
            <Link
              to="/signup"
              className="inline-flex items-center text-base font-semibold px-6 py-3 rounded-lg text-white"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              Start a 90-day pilot →
            </Link>
            <a
              href="https://calendly.com/jbouey-osiriscare/osiriscare-demo-onboard"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center text-base font-medium px-6 py-3 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50"
            >
              Book a head-to-head demo
            </a>
          </div>
          <p className="mt-6 text-xs text-slate-400 font-body">
            If you are currently on {competitorName}, we can help you run
            both platforms in parallel for 60 days so the evidence output
            is directly comparable side-by-side.
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

export default ComparePage;

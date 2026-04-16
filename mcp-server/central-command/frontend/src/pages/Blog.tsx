import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { MarketingLayout } from '../components/marketing/MarketingLayout';
import { JsonLd } from '../components/marketing/JsonLd';

/**
 * Blog — /blog index page.
 *
 * POSTS array is the single source of truth for article metadata.
 * Each blog page component (BlogHipaa2026Ops, BlogProveFast, etc.)
 * MUST appear in this list, and the title/description/slug here
 * must match the individual article's header. Adding a new post =
 * add to this list + write the page component + wire the route in
 * App.tsx. No database, no CMS, intentional.
 */
export interface BlogPostMeta {
  slug: string;
  title: string;
  excerpt: string;
  datePublished: string;
  readMinutes: number;
  tags: string[];
}

export const POSTS: BlogPostMeta[] = [
  {
    slug: '2026-hipaa-rule-for-healthcare-operations',
    title: 'The 2026 HIPAA Security Rule for Healthcare Operations Leaders',
    excerpt:
      'The NPRM will change what "compliance" means for every practice, group, and DSO. An operations-leader guide to the nine changes and the implementation timeline most organizations underestimate.',
    datePublished: '2026-04-16',
    readMinutes: 12,
    tags: ['HIPAA 2026', 'Operations', 'Risk Analysis'],
  },
  {
    slug: 'prove-hipaa-compliance-to-your-auditor-in-minutes',
    title: 'How to Prove HIPAA Compliance to Your Auditor in Minutes (Not Weeks)',
    excerpt:
      'Most audits burn three to six weeks of clinical staff time. A walkthrough of the cryptographic-evidence workflow that turns the audit into a single-session verification exercise.',
    datePublished: '2026-04-16',
    readMinutes: 9,
    tags: ['Audit', 'Evidence', 'Workflow'],
  },
  {
    slug: 'cryptographic-evidence-vs-policy-documents',
    title: 'Cryptographic Evidence vs Policy Documents: Why Auditors Are Changing Their Minds',
    excerpt:
      'The shift from "trust the vendor" to "verify the evidence" is happening across compliance-automation audits. What it looks like from the auditor\'s chair, and what practices should demand from their platform.',
    datePublished: '2026-04-16',
    readMinutes: 11,
    tags: ['Evidence', 'Auditor Perspective', 'Verification'],
  },
  {
    slug: 'multi-site-hipaa-compliance-at-dso-scale',
    title: 'Multi-site HIPAA Compliance at DSO Scale — Without a Compliance Team',
    excerpt:
      'Dental Service Organizations and multi-location provider groups face compliance at scale without the full-time compliance staff of a hospital system. A field guide to what works.',
    datePublished: '2026-04-16',
    readMinutes: 10,
    tags: ['DSO', 'Multi-site', 'Fleet Management'],
  },
];

export const Blog: React.FC = () => {
  useEffect(() => {
    document.title = 'Blog — Healthcare Compliance, HIPAA 2026, Audit Evidence | OsirisCare';
    setCanonicalAndDescription(
      'https://www.osiriscare.net/blog',
      'OsirisCare blog — HIPAA 2026 Security Rule, audit evidence workflows, multi-site compliance, and operational guides for healthcare practices, multi-location groups, and DSOs.',
    );
  }, []);

  return (
    <MarketingLayout activeNav="blog">
      <JsonLd
        data={{
          '@context': 'https://schema.org',
          '@type': 'Blog',
          name: 'OsirisCare Blog',
          description:
            'HIPAA 2026 Security Rule, audit evidence workflows, and multi-site compliance guides.',
          url: 'https://www.osiriscare.net/blog',
          blogPost: POSTS.map((p) => ({
            '@type': 'BlogPosting',
            headline: p.title,
            description: p.excerpt,
            url: `https://www.osiriscare.net/blog/${p.slug}`,
            datePublished: p.datePublished,
            author: { '@type': 'Organization', name: 'OsirisCare' },
          })),
        }}
      />

      <section className="border-b border-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-16">
          <h1 className="font-display text-4xl lg:text-5xl text-slate-900 leading-tight mb-4">
            Blog
          </h1>
          <p className="text-lg text-slate-600 font-body leading-relaxed max-w-2xl">
            Field notes on healthcare compliance — 2026 HIPAA Security Rule
            readiness, audit evidence workflows, multi-site fleet operations,
            and decisions MSPs make serving clinical customers.
          </p>
        </div>
      </section>

      <section>
        <div className="max-w-4xl mx-auto px-6 py-14">
          <ul className="divide-y divide-slate-200">
            {POSTS.map((p) => (
              <li key={p.slug} className="py-8">
                <Link to={`/blog/${p.slug}`} className="block group">
                  <p className="text-xs uppercase tracking-[0.15em] text-slate-400 font-semibold mb-2 font-body">
                    {new Date(p.datePublished).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}
                    {' · '}
                    {p.readMinutes} min read
                  </p>
                  <h2 className="font-display text-2xl lg:text-3xl text-slate-900 group-hover:text-teal-700 transition-colors mb-3">
                    {p.title}
                  </h2>
                  <p className="text-slate-600 leading-relaxed font-body mb-3">{p.excerpt}</p>
                  <div className="flex flex-wrap gap-2">
                    {p.tags.map((t) => (
                      <span
                        key={t}
                        className="text-xs text-slate-600 bg-slate-100 rounded-full px-3 py-1 font-body"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
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

export default Blog;

import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { MarketingLayout } from './MarketingLayout';
import { JsonLd } from './JsonLd';

export interface ArticleLayoutProps {
  slug: string;
  title: string;
  description: string;
  datePublished: string;
  dateModified?: string;
  readMinutes: number;
  tags: string[];
  children: React.ReactNode;
}

/**
 * ArticleLayout — shared layout for long-form blog articles.
 * Injects Article + BreadcrumbList JSON-LD per-page, sets canonical
 * and description meta tags, and provides a consistent header +
 * related-links footer so the /blog cluster reads as a coherent
 * publication surface rather than disconnected pages.
 */
export const ArticleLayout: React.FC<ArticleLayoutProps> = ({
  slug,
  title,
  description,
  datePublished,
  dateModified,
  readMinutes,
  tags,
  children,
}) => {
  const canonical = `https://www.osiriscare.net/blog/${slug}`;

  useEffect(() => {
    document.title = `${title} | OsirisCare`;
    setCanonicalAndDescription(canonical, description);
  }, [title, canonical, description]);

  return (
    <MarketingLayout activeNav="blog">
      <JsonLd
        data={{
          '@context': 'https://schema.org',
          '@type': 'Article',
          headline: title,
          description,
          author: { '@type': 'Organization', name: 'OsirisCare' },
          publisher: {
            '@type': 'Organization',
            name: 'OsirisCare',
            logo: { '@type': 'ImageObject', url: 'https://www.osiriscare.net/og-image.png' },
          },
          datePublished,
          dateModified: dateModified || datePublished,
          mainEntityOfPage: canonical,
          keywords: tags.join(', '),
        }}
      />
      <JsonLd
        data={{
          '@context': 'https://schema.org',
          '@type': 'BreadcrumbList',
          itemListElement: [
            { '@type': 'ListItem', position: 1, name: 'OsirisCare', item: 'https://www.osiriscare.net/' },
            { '@type': 'ListItem', position: 2, name: 'Blog', item: 'https://www.osiriscare.net/blog' },
            { '@type': 'ListItem', position: 3, name: title, item: canonical },
          ],
        }}
      />

      <article>
        <header className="border-b border-slate-100">
          <div className="max-w-3xl mx-auto px-6 py-14">
            <p className="text-xs uppercase tracking-[0.2em] text-teal-700 font-semibold mb-4 font-body">
              {new Date(datePublished).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}
              {' · '}
              {readMinutes} min read
            </p>
            <h1 className="font-display text-3xl lg:text-5xl text-slate-900 leading-tight mb-6">
              {title}
            </h1>
            <p className="text-lg text-slate-600 leading-relaxed font-body mb-6">{description}</p>
            <div className="flex flex-wrap gap-2">
              {tags.map((t) => (
                <span
                  key={t}
                  className="text-xs text-slate-600 bg-slate-100 rounded-full px-3 py-1 font-body"
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
        </header>

        <div className="max-w-3xl mx-auto px-6 py-14 font-body text-slate-800 leading-relaxed text-base lg:text-lg space-y-6">
          {children}
        </div>

        <footer className="bg-slate-50 border-t border-slate-100">
          <div className="max-w-3xl mx-auto px-6 py-12 text-sm font-body">
            <p className="text-slate-500 mb-4">Related</p>
            <ul className="space-y-2">
              <li>
                <Link to="/2026-hipaa-update" className="text-teal-700 hover:text-teal-900 hover:underline">
                  Full 2026 HIPAA Security Rule guide →
                </Link>
              </li>
              <li>
                <Link to="/for-msps" className="text-teal-700 hover:text-teal-900 hover:underline">
                  For MSPs and compliance partners →
                </Link>
              </li>
              <li>
                <Link to="/compare/vanta" className="text-teal-700 hover:text-teal-900 hover:underline">
                  OsirisCare vs Vanta →
                </Link>
              </li>
              <li>
                <Link to="/blog" className="text-teal-700 hover:text-teal-900 hover:underline">
                  All blog posts →
                </Link>
              </li>
            </ul>
          </div>
        </footer>
      </article>
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

export default ArticleLayout;

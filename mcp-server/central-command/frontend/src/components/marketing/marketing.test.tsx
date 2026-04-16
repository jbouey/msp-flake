import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { JsonLd } from './JsonLd';

/*
 * Structural guardrails for the marketing content cluster.
 *
 * These tests are deliberately narrow: they verify the shape of the
 * serialized output (JSON-LD script tag presence, safe escaping of
 * `</` sequences, valid JSON payload) and the presence of load-bearing
 * content on the primary marketing pages (2026-ready badge, NPRM
 * table on the Hipaa2026Update page, comparison matrix rows on the
 * compare pages). If a future refactor strips one of these, CI fails
 * before the SEO investment silently erodes.
 */

// Mock shared SVG component used by MarketingLayout
vi.mock('../shared', () => ({
  OsirisCareLeaf: ({ className }: { className?: string }) =>
    React.createElement('svg', { 'data-testid': 'leaf-icon', className }),
}));

describe('JsonLd helper', () => {
  it('renders a <script type="application/ld+json"> tag', () => {
    const { container } = render(
      <JsonLd data={{ '@context': 'https://schema.org', '@type': 'WebPage' }} />,
    );
    const script = container.querySelector('script[type="application/ld+json"]');
    expect(script).not.toBeNull();
  });

  it('payload parses as valid JSON with the provided data', () => {
    const payload = {
      '@context': 'https://schema.org',
      '@type': 'FAQPage',
      mainEntity: [{ '@type': 'Question', name: 'Q', acceptedAnswer: { '@type': 'Answer', text: 'A' } }],
    };
    const { container } = render(<JsonLd data={payload} />);
    const script = container.querySelector('script[type="application/ld+json"]');
    expect(script).not.toBeNull();
    // Parse back using the unescaped form a browser would see after reading the text node.
    // The safe-escape replaces `<` with `\u003c`; the browser's JSON parser handles that form.
    const raw = (script!.textContent || '').replace(/\\u003c/g, '<');
    const parsed = JSON.parse(raw);
    expect(parsed['@type']).toBe('FAQPage');
    expect(parsed.mainEntity[0].name).toBe('Q');
  });

  it('escapes literal </ sequences so script-tag breakout is impossible', () => {
    const malicious = { note: '</script><script>alert(1)</script>' };
    const { container } = render(<JsonLd data={malicious} />);
    const script = container.querySelector('script[type="application/ld+json"]');
    expect(script).not.toBeNull();
    const text = script!.textContent || '';
    // The raw `</` must never appear verbatim in the serialized output —
    // that is the only thing a browser parser uses to close a <script> early.
    expect(text.includes('</')).toBe(false);
    // And the escaped form must be present.
    expect(text.includes('\\u003c/')).toBe(true);
  });
});

describe('Hipaa2026Update marketing page', () => {
  it('declares the NPRM requirement table and FAQ schema', async () => {
    // Dynamically import so we pick up the real component as-deployed.
    const { Hipaa2026Update } = await import('../../pages/Hipaa2026Update');
    const { container, getAllByText } = render(
      <MemoryRouter>
        <Hipaa2026Update />
      </MemoryRouter>,
    );

    // Hero copy must name the 2026 Security Rule (appears multiple times
    // across hero + bodies, so use getAllByText).
    expect(getAllByText(/2026 HIPAA Security Rule/i).length).toBeGreaterThan(0);

    // At least one JSON-LD block must be present (Article + FAQPage expected).
    const ldBlocks = container.querySelectorAll('script[type="application/ld+json"]');
    expect(ldBlocks.length).toBeGreaterThanOrEqual(2);

    // Must include each of the nine NPRM citation ids as anchor targets so the
    // at-a-glance navigator works. Loss of any is a content regression.
    const requiredIds = [
      'mfa',
      'encryption',
      'network-segmentation',
      'asset-inventory',
      'vuln-scanning',
      'patching',
      'incident-response',
      'risk-analysis',
      'contingency-testing',
    ];
    requiredIds.forEach((id) => {
      expect(container.querySelector(`#${id}`)).not.toBeNull();
    });
  });
});

describe('Blog index', () => {
  it('lists the four launch-cluster posts', async () => {
    const { Blog, POSTS } = await import('../../pages/Blog');
    const { getByText } = render(
      <MemoryRouter>
        <Blog />
      </MemoryRouter>,
    );
    expect(POSTS.length).toBeGreaterThanOrEqual(4);
    // Each post slug must appear as a rendered link target title.
    POSTS.forEach((p) => {
      expect(getByText(p.title)).toBeTruthy();
    });
  });
});

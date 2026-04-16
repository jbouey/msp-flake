import React from 'react';

/**
 * JsonLd — renders a <script type="application/ld+json"> with a typed
 * JSON payload as text content. This is the React-safe way to inject
 * structured data: the payload goes through JSON.stringify (strings
 * are escaped) and is rendered as a text node — no HTML interpretation,
 * no XSS surface, no unsafe inner-html APIs.
 *
 * Use one <JsonLd> per schema object. Search engines parse each
 * <script> block independently.
 */
/**
 * Defense-in-depth: even though JSON.stringify escapes strings, it does
 * NOT escape literal `</` sequences. If any schema field ever contained
 * the exact string `</script>`, the browser would close the script
 * element prematurely. Replace `<` with its unicode escape so payloads
 * are never able to break out of the script tag regardless of contents.
 */
function safeJsonLd(data: object): string {
  return JSON.stringify(data).replace(/</g, '\\u003c');
}

export const JsonLd: React.FC<{ data: object }> = ({ data }) => (
  <script type="application/ld+json">{safeJsonLd(data)}</script>
);

export default JsonLd;

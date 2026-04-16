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
export const JsonLd: React.FC<{ data: object }> = ({ data }) => (
  <script type="application/ld+json">{JSON.stringify(data)}</script>
);

export default JsonLd;

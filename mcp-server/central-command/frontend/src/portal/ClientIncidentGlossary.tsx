/**
 * ClientIncidentGlossary — Session 206 round-table P2.
 *
 * Plain-language glossary card surfaced directly on the client portal.
 * Round-table: the end customer sees "SCREEN_LOCK" with no idea what
 * it means. Exposing the glossary inline preempts that confusion.
 *
 * Uses incidentGlossary.ts as the data source so the same explanations
 * can be reused anywhere an incident_type is rendered (PortalAlerts
 * detail pages, email digests, etc.).
 */

import React, { useState } from 'react';
import { GLOSSARY, Explanation } from './incidentGlossary';

const FEATURED = [
  'SCREEN_LOCK',
  'PATCH_MISSING',
  'BACKUP_MISSING',
  'BITLOCKER_MISSING',
  'MFA_MISSING',
  'APPLIANCE_OFFLINE',
];

export const ClientIncidentGlossary: React.FC = () => {
  const [showAll, setShowAll] = useState(false);
  const featured = FEATURED
    .map((k) => ({ key: k, value: GLOSSARY[k] }))
    .filter((e): e is { key: string; value: Explanation } => Boolean(e.value));
  const rest = Object.entries(GLOSSARY)
    .filter(([k]) => !FEATURED.includes(k))
    .map(([key, value]) => ({ key, value }));

  const items = showAll ? [...featured, ...rest] : featured;

  return (
    <div className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xl">📖</span>
          <h3 className="text-sm font-semibold text-white/80">What each check means</h3>
        </div>
        <button
          type="button"
          onClick={() => setShowAll((x) => !x)}
          className="text-[11px] text-blue-400 hover:text-blue-300"
        >
          {showAll ? 'Show less' : `Show all ${featured.length + rest.length}`}
        </button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3">
        {items.map(({ key, value }) => (
          <div key={key}>
            <div className="text-sm text-white/90 font-medium">{value.title}</div>
            <div className="text-[11px] text-white/60 mt-0.5 leading-relaxed">
              {value.why_it_matters}
            </div>
            <div className="text-[10px] text-white/30 font-mono mt-0.5">{key}</div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ClientIncidentGlossary;

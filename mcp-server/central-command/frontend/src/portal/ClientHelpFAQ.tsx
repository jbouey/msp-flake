/**
 * ClientHelpFAQ — Session 206 round-table P2.
 *
 * Plain-language FAQ embedded directly on the client portal so the
 * practice manager doesn't have to click out to a knowledge base.
 * Specifically tuned to the concerns the round-table called out:
 *   - "is my patient data safe?"
 *   - "what do I do during an audit?"
 *   - "how do I know it's working?"
 */

import React, { useState } from 'react';

interface QA { q: string; a: React.ReactNode }

const ITEMS: QA[] = [
  {
    q: 'Does OsirisCare ever see patient information?',
    a: (
      <>
        No. All PHI (names, DOBs, record IDs) is scrubbed at your on-site appliance <i>before</i> any
        data leaves your network. Our Central Command only receives non-PHI operational telemetry.
        The monthly compliance packet is the authoritative record of what was and wasn't transmitted.
      </>
    ),
  },
  {
    q: 'My auditor wants proof. What do I give them?',
    a: (
      <>
        Click <b>"Download proof package"</b> at the top of this page. That ZIP contains the entire
        cryptographic evidence chain for your site plus a <code className="px-1 bg-white/10 rounded">verify.sh</code>{' '}
        script your auditor runs on their own laptop. No OsirisCare servers are involved in the verification.
      </>
    ),
  },
  {
    q: 'How do I know the platform is actually working?',
    a: (
      <>
        The green "You are protected" banner at the top of this page updates in real-time based on
        live check-ins, evidence freshness, and open incidents. Green = all three healthy; amber =
        something needs attention.
      </>
    ),
  },
  {
    q: 'What if I see something I don\'t understand?',
    a: (
      <>
        Click "Email your partner" — your partner organization is there to translate anything
        technical. They review this same dashboard on your behalf.
      </>
    ),
  },
  {
    q: 'Can I turn off the email notifications?',
    a: (
      <>
        Yes. Scroll down to <b>Email preferences</b>, uncheck what you don't want. Critical
        alerts are opt-in too, though we recommend leaving them on.
      </>
    ),
  },
  {
    q: 'How long are my compliance records kept?',
    a: (
      <>
        Monthly compliance packets are retained for 7 years per HIPAA §164.316(b)(2)(i) — 1 year
        longer than the 6-year legal minimum. Evidence bundles are immutable — deletion is blocked
        at the database layer.
      </>
    ),
  },
];

export const ClientHelpFAQ: React.FC = () => {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <div id="faq" className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-6 max-w-3xl mx-auto">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xl">❓</span>
        <h3 className="text-sm font-semibold text-white/80">Common questions</h3>
      </div>
      <div className="divide-y divide-white/5">
        {ITEMS.map((item, idx) => (
          <div key={idx} className="py-2">
            <button
              type="button"
              onClick={() => setOpen(open === idx ? null : idx)}
              className="w-full flex items-center justify-between text-left text-sm text-white/90 py-1 hover:text-white"
              aria-expanded={open === idx}
            >
              <span>{item.q}</span>
              <span className="text-white/40 text-xs ml-2">{open === idx ? '−' : '+'}</span>
            </button>
            {open === idx && (
              <div className="mt-1 text-[13px] text-white/70 leading-relaxed">
                {item.a}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

export default ClientHelpFAQ;

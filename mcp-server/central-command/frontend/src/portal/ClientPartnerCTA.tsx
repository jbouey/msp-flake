/**
 * ClientPartnerCTA — Session 206 round-table P1.
 *
 * Explicit "need help? your partner is here" card so the practice
 * manager always has a named human to email. Appears whether or not
 * the site is protected — support questions are not just about
 * incidents.
 */

import React from 'react';

interface Props {
  partnerName: string | null;
  partnerEmail: string | null;
  lastReviewedAt: string | null;
}

function formatReviewAgo(iso: string | null): string {
  if (!iso) return '';
  const days = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86400000));
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  return `${months} month${months === 1 ? '' : 's'} ago`;
}

export const ClientPartnerCTA: React.FC<Props> = ({ partnerName, partnerEmail, lastReviewedAt }) => {
  if (!partnerName && !partnerEmail) return null;
  const subject = encodeURIComponent('Question about our HIPAA compliance platform');
  const body = encodeURIComponent(
    'Hi — I\'m looking at our OsirisCare compliance portal and wanted to ask:\n\n'
  );
  const mailto = partnerEmail ? `mailto:${partnerEmail}?subject=${subject}&body=${body}` : undefined;
  return (
    <div className="rounded-2xl bg-gradient-to-br from-blue-500/10 to-indigo-500/5 backdrop-blur-xl border border-white/10 p-6 max-w-3xl mx-auto">
      <div className="flex items-start gap-3">
        <span className="text-2xl">🤝</span>
        <div className="flex-1">
          <h3 className="text-sm font-semibold text-white/90">Need help?</h3>
          <p className="text-xs text-white/70 mt-1">
            Your IT partner {partnerName && <b className="text-white">{partnerName}</b>}{' '}
            is watching over this system with you.
            {lastReviewedAt && (
              <> They last reviewed your site <b>{formatReviewAgo(lastReviewedAt)}</b>.</>
            )}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {mailto && (
              <a
                href={mailto}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-500 hover:bg-blue-600 text-white text-xs font-semibold rounded transition-colors"
              >
                ✉ Email your partner
              </a>
            )}
            <a
              href="#faq"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white/10 hover:bg-white/20 text-white text-xs rounded transition-colors"
            >
              Common questions
            </a>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ClientPartnerCTA;

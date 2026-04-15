import React, { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { OsirisCareLeaf } from '../components/shared';
import { BRANDING } from '../constants';

interface SessionState {
  signup_id: string;
  email: string;
  practice_name: string;
  plan: string;
  plan_details?: { display_name: string };
  completed_at: string | null;
}

export const SignupComplete: React.FC = () => {
  const [searchParams] = useSearchParams();
  const signupId = searchParams.get('signup_id') || '';
  const [session, setSession] = useState<SessionState | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    document.title = `Welcome to ${BRANDING.name}`;
    if (!signupId) {
      setLoading(false);
      return;
    }
    // Stripe's webhook may land before the user gets here. Poll up
    // to 10 times (20s total) for completed_at to flip, so the page
    // shows a settled state instead of a mid-provision limbo.
    let tries = 0;
    const poll = () => {
      tries += 1;
      fetch(`/api/billing/signup/session/${encodeURIComponent(signupId)}`)
        .then((r) => r.json())
        .then((data) => {
          setSession(data);
          if (data.completed_at || tries >= 10) {
            setLoading(false);
          } else {
            setTimeout(poll, 2000);
          }
        })
        .catch(() => setLoading(false));
    };
    poll();
  }, [signupId]);

  return (
    <div className="min-h-screen bg-white" style={{ fontFamily: "'DM Sans', 'Helvetica Neue', system-ui, sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Serif+Display&display=swap');
        .font-display { font-family: 'DM Serif Display', Georgia, serif; }
        .font-body { font-family: 'DM Sans', 'Helvetica Neue', system-ui, sans-serif; }
      `}</style>

      <nav className="sticky top-0 z-50 border-b border-slate-100 bg-white/95">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg flex items-center justify-center"
                 style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}>
              <OsirisCareLeaf className="w-5 h-5" color="white" />
            </div>
            <span className="text-lg font-semibold text-slate-900 tracking-tight font-body">
              {BRANDING.name}
            </span>
          </Link>
        </div>
      </nav>

      <div className="max-w-3xl mx-auto px-6 py-20">
        <div className="text-center mb-10">
          <div className="mx-auto w-16 h-16 rounded-full flex items-center justify-center mb-6"
               style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}>
            <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <p className="text-sm font-semibold uppercase tracking-widest mb-2 font-body" style={{ color: '#0d9488' }}>
            Step 3 of 3 · Complete
          </p>
          <h1 className="font-display text-3xl md:text-4xl text-slate-900 mb-3">Welcome aboard</h1>
          {loading && (
            <p className="text-base text-slate-500 font-body font-light">
              Verifying with Stripe…
            </p>
          )}
          {!loading && session && (
            <p className="text-base text-slate-500 font-body font-light">
              Payment received for <span className="font-semibold text-slate-700">{session.practice_name}</span>.
              Your substrate is being provisioned.
            </p>
          )}
        </div>

        <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 mb-6">
          <h2 className="text-sm font-semibold text-slate-900 font-body mb-4">What happens next</h2>
          <ol className="space-y-3 text-sm text-slate-700 font-body">
            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full bg-teal-100 text-teal-700 font-semibold flex items-center justify-center text-xs">1</span>
              <span>We'll email <code className="text-teal-700">{session?.email ?? 'you'}</code> within 5 minutes with your appliance shipping details and install guide.</span>
            </li>
            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full bg-teal-100 text-teal-700 font-semibold flex items-center justify-center text-xs">2</span>
              <span>Your appliance ships in 1-2 business days. Boot it on your practice network and it provisions itself.</span>
            </li>
            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full bg-teal-100 text-teal-700 font-semibold flex items-center justify-center text-xs">3</span>
              <span>Your first signed evidence bundle drops in ~15 minutes after the appliance checks in.</span>
            </li>
            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full bg-teal-100 text-teal-700 font-semibold flex items-center justify-center text-xs">4</span>
              <span>Your portal login + auditor kit access are sent via the same welcome email.</span>
            </li>
          </ol>
        </div>

        <div className="rounded-xl border border-slate-200 p-6 mb-6">
          <h3 className="text-sm font-semibold text-slate-900 font-body mb-2">Your commitment</h3>
          <p className="text-sm text-slate-600 font-body leading-relaxed">
            You can cancel any time from the billing portal in your client dashboard.
            Every evidence bundle generated for your practice remains downloadable via the
            auditor kit — including after cancellation. Walk-away rights are non-negotiable.
          </p>
        </div>

        <div className="text-center">
          <p className="text-xs text-slate-400 font-body mb-4">
            Questions? <a href="mailto:support@osiriscare.net" className="text-teal-700 hover:underline">support@osiriscare.net</a>
          </p>
          <Link to="/" className="text-sm text-slate-500 hover:text-slate-900 font-body">
            Return to home
          </Link>
        </div>
      </div>
    </div>
  );
};

export default SignupComplete;

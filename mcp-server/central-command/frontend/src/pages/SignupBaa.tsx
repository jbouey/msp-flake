import React, { useState, useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { OsirisCareLeaf } from '../components/shared';
import { BRANDING } from '../constants';
import { csrfHeaders } from '../utils/csrf';

// Short, stable acknowledgment statement that gets SHA256-hashed and
// stored with the signature. The FULL BAA lives at /legal/baa —
// updating that text doesn't invalidate past signatures because the
// BAA version is bumped separately in backend config.
const ACKNOWLEDGMENT_TEXT = `By typing my name and clicking "I agree" I acknowledge:
1. I have read and agree to the OsirisCare Business Associate Agreement (BAA) at /legal/baa, version v1.0-2026-04-15.
2. I am authorized to bind my practice to this agreement.
3. I understand that OsirisCare processes compliance evidence at the on-premise appliance and does not transmit Protected Health Information to central infrastructure.
4. I understand that this signature is recorded with my IP address, user agent, and timestamp as part of the HIPAA audit trail.`;

async function sha256Hex(text: string): Promise<string> {
  const bytes = new globalThis.TextEncoder().encode(text);
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

interface SessionState {
  signup_id: string;
  email: string;
  practice_name: string;
  plan: string;
  plan_details?: { display_name: string; description: string };
  baa_signed_at: string | null;
  completed_at: string | null;
  expired: boolean;
  baa_version: string;
}

export const SignupBaa: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const signupId = searchParams.get('signup_id') || '';

  const [session, setSession] = useState<SessionState | null>(null);
  const [loading, setLoading] = useState(true);
  const [signerName, setSignerName] = useState('');
  const [readConfirmed, setReadConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = `Business Associate Agreement · ${BRANDING.name}`;
    if (!signupId) {
      setError('Missing signup_id — please restart from /signup.');
      setLoading(false);
      return;
    }
    fetch(`/api/billing/signup/session/${encodeURIComponent(signupId)}`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || `HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((data) => {
        setSession(data);
        if (data.completed_at) {
          navigate(`/signup/complete?signup_id=${encodeURIComponent(signupId)}`);
        } else if (data.expired) {
          setError('Signup session expired. Please restart from /signup.');
        } else if (data.baa_signed_at) {
          // Already signed — skip to checkout-create
          setReadConfirmed(true);
        }
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'unknown error');
      })
      .finally(() => setLoading(false));
  }, [signupId, navigate]);

  const onSignAndCheckout = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      // Only sign BAA if not already signed
      if (!session?.baa_signed_at) {
        const hash = await sha256Hex(ACKNOWLEDGMENT_TEXT);
        const signRes = await fetch('/api/billing/signup/sign-baa', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
          body: JSON.stringify({
            signup_id: signupId,
            signer_name: signerName,
            baa_text_sha256: hash,
          }),
        });
        if (!signRes.ok) {
          const body = await signRes.json().catch(() => ({}));
          throw new Error(body.detail || `HTTP ${signRes.status}`);
        }
      }

      // Create Checkout session
      const origin = window.location.origin;
      const checkoutRes = await fetch('/api/billing/signup/checkout', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({
          signup_id: signupId,
          success_url: `${origin}/signup/complete?signup_id=${encodeURIComponent(signupId)}`,
          cancel_url: `${origin}/signup/baa?signup_id=${encodeURIComponent(signupId)}`,
        }),
      });
      if (!checkoutRes.ok) {
        const body = await checkoutRes.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${checkoutRes.status}`);
      }
      const data = await checkoutRes.json();
      // Redirect to Stripe-hosted Checkout
      window.location.href = data.checkout_url;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'unknown error');
      setSubmitting(false);
    }
  };

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

      <div className="max-w-3xl mx-auto px-6 py-16">
        <div className="mb-10">
          <p className="text-sm font-semibold uppercase tracking-widest mb-2 font-body" style={{ color: '#0d9488' }}>
            Step 2 of 3
          </p>
          <h1 className="font-display text-3xl md:text-4xl text-slate-900 mb-3">Business Associate Agreement</h1>
          <p className="text-base text-slate-500 font-body font-light">
            Required by HIPAA before OsirisCare handles any covered relationship. Sign once; we keep the signed record for audit.
          </p>
        </div>

        {loading && (
          <p className="text-sm text-slate-500 font-body">Loading session…</p>
        )}

        {!loading && session && (
          <>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 mb-6">
              <div className="text-xs uppercase tracking-wider text-slate-500 font-body mb-2">Signing for</div>
              <div className="text-lg font-semibold text-slate-900 font-body">{session.practice_name}</div>
              <div className="text-sm text-slate-600 font-body">{session.email}</div>
              <div className="mt-2 text-xs text-slate-500 font-body">
                Plan: <span className="font-semibold">{session.plan_details?.display_name ?? session.plan}</span>
                {' · '}
                BAA version: <code className="text-teal-700">{session.baa_version}</code>
              </div>
            </div>

            <div className="rounded-xl border border-slate-200 p-6 mb-6">
              <h2 className="text-sm font-semibold text-slate-900 font-body mb-3">Acknowledgment</h2>
              <pre className="whitespace-pre-wrap text-sm text-slate-700 font-body leading-relaxed mb-4">
{ACKNOWLEDGMENT_TEXT}
              </pre>
              <Link
                to="/legal/baa"
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-teal-700 hover:underline font-body"
              >
                Read the full BAA → /legal/baa
              </Link>
            </div>

            <form onSubmit={onSignAndCheckout} className="space-y-4">
              <div>
                <label htmlFor="signer" className="block text-sm font-semibold text-slate-900 mb-1 font-body">
                  Type your full legal name to sign
                </label>
                <input
                  id="signer"
                  type="text"
                  required
                  maxLength={255}
                  autoComplete="name"
                  disabled={!!session.baa_signed_at}
                  value={signerName || (session.baa_signed_at ? '(already signed)' : '')}
                  onChange={(e) => setSignerName(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-4 py-3 text-sm font-body focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-teal-500 disabled:bg-slate-100"
                  placeholder="Your full legal name"
                />
              </div>

              {!session.baa_signed_at && (
                <label className="flex items-start gap-3 text-sm text-slate-700 font-body">
                  <input
                    type="checkbox"
                    checked={readConfirmed}
                    onChange={(e) => setReadConfirmed(e.target.checked)}
                    className="mt-0.5"
                  />
                  <span>
                    I have read the <Link to="/legal/baa" target="_blank" className="text-teal-700 hover:underline">full BAA</Link> and
                    agree to the acknowledgment above. I am authorized to bind my practice.
                  </span>
                </label>
              )}

              {error && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 font-body">
                  {error}
                </div>
              )}

              <div className="flex items-center justify-between pt-2">
                <Link to="/signup" className="text-sm text-slate-500 hover:text-slate-900 font-body">
                  Back
                </Link>
                <button
                  type="submit"
                  disabled={
                    submitting ||
                    (!session.baa_signed_at && (!signerName || !readConfirmed))
                  }
                  className="px-6 py-3 rounded-lg text-sm font-semibold text-white transition-all font-body disabled:opacity-60"
                  style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
                >
                  {submitting ? 'Redirecting to Stripe…' : 'I agree — continue to payment'}
                </button>
              </div>
            </form>
          </>
        )}

        {!loading && error && !session && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 font-body">
            {error}
          </div>
        )}
      </div>
    </div>
  );
};

export default SignupBaa;

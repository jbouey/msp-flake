import React, { useState, useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { OsirisCareLeaf } from '../components/shared';
import { BRANDING } from '../constants';

const PLANS: Record<string, { name: string; price: string; cadence: string; description: string }> = {
  pilot: {
    name: '90-Day Pilot',
    price: '$299',
    cadence: 'one-time',
    description: 'Full Essentials access for 90 days with an on-premise appliance. No auto-conversion — upgrade manually or return the appliance at day 90.',
  },
  essentials: {
    name: 'Essentials',
    price: '$499',
    cadence: 'per month',
    description: '59 compliance checks, L1 auto-healing, evidence bundles, client portal.',
  },
  professional: {
    name: 'Professional',
    price: '$799',
    cadence: 'per month',
    description: '+ L2 LLM healing, full runbook library, peer-witnessed evidence.',
  },
  enterprise: {
    name: 'Enterprise',
    price: '$1,299',
    cadence: 'per month',
    description: '+ dedicated L3 escalation (4hr SLA), audit preparation support, custom runbooks.',
  },
};

const US_STATES: string[] = [
  'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
  'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
  'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
  'VA','WA','WV','WI','WY','DC',
];

interface PartnerInvitePayload {
  invite_id: string;
  partner_name: string;
  partner_slug: string;
  partner_brand: string | null;
  plan: string;
  clinic_email: string | null;
  clinic_name: string | null;
  expires_at: string | null;
}

export const Signup: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const inviteToken = searchParams.get('invite');

  const initialPlan = searchParams.get('plan') || 'pilot';
  const [plan, setPlan] = useState(initialPlan in PLANS ? initialPlan : 'pilot');
  const [email, setEmail] = useState('');
  const [practiceName, setPracticeName] = useState('');
  const [billingContactName, setBillingContactName] = useState('');
  const [state, setState] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [invite, setInvite] = useState<PartnerInvitePayload | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteLoading, setInviteLoading] = useState<boolean>(!!inviteToken);

  useEffect(() => {
    document.title = `Sign up · ${BRANDING.name}`;
  }, []);

  // Validate partner invite token if present in URL.
  useEffect(() => {
    if (!inviteToken) return;
    let aborted = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/partner-invites/${encodeURIComponent(inviteToken)}/validate`,
        );
        if (aborted) return;
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          const msg =
            res.status === 404
              ? 'This invite link is not recognized.'
              : res.status === 409
                ? 'This invite has already been used.'
                : res.status === 410
                  ? 'This invite has expired or been revoked.'
                  : body.detail || `HTTP ${res.status}`;
          setInviteError(msg);
          return;
        }
        const data: PartnerInvitePayload = await res.json();
        setInvite(data);
        // Preselect plan from invite (overrides ?plan= when both present).
        if (data.plan in PLANS) {
          setPlan(data.plan);
        }
        // Pre-fill clinic email / name from the invite as hints.
        if (data.clinic_email) setEmail((prev) => prev || data.clinic_email!);
        if (data.clinic_name) setPracticeName((prev) => prev || data.clinic_name!);
      } catch (e) {
        if (!aborted) {
          setInviteError(e instanceof Error ? e.message : 'invite validation failed');
        }
      } finally {
        if (!aborted) setInviteLoading(false);
      }
    })();
    return () => {
      aborted = true;
    };
  }, [inviteToken]);

  const planInfo = PLANS[plan];

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const body: Record<string, unknown> = {
        email,
        practice_name: practiceName,
        billing_contact_name: billingContactName,
        state: state || null,
        plan,
      };
      // Only thread the token if it validated — prevents a bad token from
      // tripping the backend's fail-fast check on every retry.
      if (invite && inviteToken) {
        body.partner_invite_token = inviteToken;
      }
      const res = await fetch('/api/billing/signup/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const resBody = await res.json().catch(() => ({}));
        throw new Error(resBody.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      navigate(`/signup/baa?signup_id=${encodeURIComponent(data.signup_id)}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'unknown error');
    } finally {
      setSubmitting(false);
    }
  };

  const partnerLabel = invite?.partner_brand || invite?.partner_name || null;

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
          <Link to="/pricing" className="text-sm text-slate-500 hover:text-slate-900 transition-colors font-body">
            Back to pricing
          </Link>
        </div>
      </nav>

      <div className="max-w-3xl mx-auto px-6 py-16">
        {inviteLoading && (
          <div className="mb-6 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600 font-body">
            Validating your invite link…
          </div>
        )}

        {inviteError && (
          <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 font-body">
            <strong>Invite link problem:</strong> {inviteError} You can still continue — you'll just
            sign up directly with OsirisCare instead of through your MSP partner.
          </div>
        )}

        {invite && partnerLabel && (
          <div className="mb-6 rounded-lg border border-teal-200 bg-teal-50 px-4 py-3 font-body">
            <p className="text-sm font-semibold text-teal-900">
              Invited by {partnerLabel}
            </p>
            <p className="mt-0.5 text-xs text-teal-800 leading-relaxed">
              Your compliance appliance will be managed by <strong>{invite.partner_name}</strong> as
              your MSP. OsirisCare is the attestation substrate — {invite.partner_name} holds the
              operating relationship and your direct Business Associate Agreement.
            </p>
          </div>
        )}

        <div className="mb-10">
          <p className="text-sm font-semibold uppercase tracking-widest mb-2 font-body" style={{ color: '#0d9488' }}>
            Step 1 of 3
          </p>
          <h1 className="font-display text-3xl md:text-4xl text-slate-900 mb-3">Tell us about your practice</h1>
          <p className="text-base text-slate-500 font-body font-light">
            Takes about 30 seconds. Next you'll review the Business Associate Agreement, then payment.
          </p>
        </div>

        <form onSubmit={onSubmit} className="space-y-6">
          {/* Plan selection */}
          <div>
            <label className="block text-sm font-semibold text-slate-900 mb-3 font-body">
              Plan
              {invite && (
                <span className="ml-2 text-xs font-normal text-slate-500">
                  (preselected by {partnerLabel})
                </span>
              )}
            </label>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {(['pilot', 'essentials', 'professional', 'enterprise'] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPlan(p)}
                  className={`text-left rounded-lg border p-4 transition-all font-body ${
                    plan === p
                      ? 'border-2 border-teal-500 bg-teal-50/40'
                      : 'border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <div className="flex items-baseline justify-between">
                    <div className="font-semibold text-slate-900">{PLANS[p].name}</div>
                    <div className="text-sm tabular-nums text-slate-700">
                      <span className="font-semibold">{PLANS[p].price}</span>
                      <span className="ml-1 text-slate-500">{PLANS[p].cadence}</span>
                    </div>
                  </div>
                  <div className="mt-1 text-xs text-slate-500 leading-snug">{PLANS[p].description}</div>
                </button>
              ))}
            </div>
            {planInfo && (
              <p className="mt-3 text-xs text-slate-500 font-body">
                {plan === 'pilot'
                  ? 'Paid once. 90 days full access. No auto-renew. Return appliance or upgrade at day 90.'
                  : 'Monthly billing. Cancel anytime via the Stripe billing portal. Evidence bundles remain downloadable after cancellation.'}
              </p>
            )}
          </div>

          {/* Email */}
          <div>
            <label htmlFor="email" className="block text-sm font-semibold text-slate-900 mb-1 font-body">
              Billing email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-4 py-3 text-sm font-body focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-teal-500"
              placeholder="you@yourpractice.com"
            />
          </div>

          {/* Practice name */}
          <div>
            <label htmlFor="practice" className="block text-sm font-semibold text-slate-900 mb-1 font-body">
              Practice legal name
            </label>
            <input
              id="practice"
              type="text"
              required
              maxLength={255}
              value={practiceName}
              onChange={(e) => setPracticeName(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-4 py-3 text-sm font-body focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-teal-500"
              placeholder="Smith Family Dental, LLC"
            />
            <p className="mt-1 text-xs text-slate-500 font-body">
              As it appears on your business registration. Used on the BAA and your receipts.
            </p>
          </div>

          {/* Billing contact name */}
          <div>
            <label htmlFor="contact" className="block text-sm font-semibold text-slate-900 mb-1 font-body">
              Billing contact name
            </label>
            <input
              id="contact"
              type="text"
              required
              maxLength={255}
              autoComplete="name"
              value={billingContactName}
              onChange={(e) => setBillingContactName(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-4 py-3 text-sm font-body focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-teal-500"
              placeholder="Your name"
            />
          </div>

          {/* State (for tax) */}
          <div>
            <label htmlFor="state" className="block text-sm font-semibold text-slate-900 mb-1 font-body">
              State (for sales tax)
            </label>
            <select
              id="state"
              value={state}
              onChange={(e) => setState(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-4 py-3 text-sm font-body focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-teal-500"
            >
              <option value="">Select…</option>
              {US_STATES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 font-body">
              {error}
            </div>
          )}

          <div className="flex items-center justify-between pt-2">
            <Link to="/pricing" className="text-sm text-slate-500 hover:text-slate-900 font-body">
              Cancel
            </Link>
            <button
              type="submit"
              disabled={submitting}
              className="px-6 py-3 rounded-lg text-sm font-semibold text-white transition-all font-body disabled:opacity-60"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              {submitting ? 'Working…' : 'Continue to BAA'}
            </button>
          </div>
        </form>

        <p className="mt-8 text-xs text-slate-400 font-body leading-relaxed">
          OsirisCare billing systems do not process or store PHI. All patient data stays
          at your on-premise appliance. A Business Associate Agreement is required for
          operating the substrate itself and is signed on the next step.
        </p>
      </div>
    </div>
  );
};

export default Signup;

import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { BRANDING } from '../constants';

type ConsumeStatus =
  | 'validating'
  | 'awaiting_reason'
  | 'submitting'
  | 'success'
  | 'unauthenticated'
  | 'forbidden'
  | 'error';

interface ConsumeState {
  status: ConsumeStatus;
  message?: string;
  detail?: string;
  request_id?: string;
}

const ALLOWED_ACTIONS = new Set(['approve', 'reject']);

export const PrivilegedAccessAct: React.FC = () => {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('t') || '';
  const action = (searchParams.get('action') || '').toLowerCase();
  const requestId = searchParams.get('rid') || '';

  const [state, setState] = useState<ConsumeState>({ status: 'validating' });
  const [rejectionReason, setRejectionReason] = useState('');
  const triedRef = useRef(false);

  useEffect(() => {
    if (triedRef.current) return;
    if (!token || token.length < 16) {
      setState({
        status: 'error',
        message: 'Missing or malformed link.',
        detail: 'The URL did not include a valid token. Open the email again and click the link directly.',
      });
      return;
    }
    if (!ALLOWED_ACTIONS.has(action)) {
      setState({
        status: 'error',
        message: 'Unrecognized action.',
        detail: 'This link is not valid. Open the email again and use the Approve or Reject button.',
      });
      return;
    }

    if (action === 'reject') {
      // Wait for the operator to type a reason before consuming the
      // single-use token. Approvals consume immediately.
      triedRef.current = true;
      setState({ status: 'awaiting_reason', request_id: requestId });
      return;
    }

    triedRef.current = true;
    void consume(undefined);
  }, [token, action, requestId]);

  const consume = async (reason: string | undefined): Promise<void> => {
    setState((prev) => ({ ...prev, status: 'submitting' }));
    try {
      const resp = await fetch('/api/client/privileged-access/magic-link/consume', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token,
          rejection_reason: reason,
        }),
      });

      if (resp.status === 200) {
        const data = await resp.json().catch(() => ({}));
        setState({
          status: 'success',
          message: action === 'approve' ? 'Privileged access approved.' : 'Privileged access rejected.',
          detail:
            action === 'approve'
              ? 'The technician has been authorized to perform the requested change. A signed attestation bundle has been written to the evidence chain.'
              : 'The request has been denied. A signed attestation bundle has been written to the evidence chain.',
          request_id: data.request_id || requestId,
        });
        return;
      }

      let detail = '';
      try {
        const j = await resp.json();
        detail = j?.detail || '';
      } catch {
        detail = await resp.text();
      }

      if (resp.status === 401) {
        setState({
          status: 'unauthenticated',
          message: 'Sign in to your client portal first.',
          detail:
            'Open your client portal in another tab, sign in with your normal email + password, then return to this tab and refresh.',
        });
        return;
      }
      if (resp.status === 403) {
        setState({
          status: 'forbidden',
          message: 'Your account does not have admin access for this site.',
          detail:
            'Magic-link approvals require an Owner or Admin role. Ask your client admin to act on this request, or sign in as the listed approver.',
        });
        return;
      }
      setState({
        status: 'error',
        message: 'This link cannot be used.',
        detail:
          detail ||
          'The token may have expired (30-minute window), already been used, or been issued to a different user.',
      });
    } catch (e) {
      setState({
        status: 'error',
        message: 'Network error.',
        detail: 'Could not reach the server. Check your connection and try again.',
      });
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
      <div className="max-w-xl w-full">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-slate-900">{BRANDING.name}</h1>
          <p className="text-slate-600 mt-2">Privileged Access Authorization</p>
        </div>

        <div className="bg-white rounded-2xl shadow-lg p-8">
          {(state.status === 'validating' || state.status === 'submitting') && (
            <div className="text-center">
              <div className="w-10 h-10 mx-auto border-4 border-blue-500 border-t-transparent rounded-full animate-spin mb-4" />
              <h2 className="text-lg font-semibold text-slate-900">
                {state.status === 'validating' ? 'Validating link…' : 'Recording your decision…'}
              </h2>
              <p className="text-sm text-slate-500 mt-2">
                Single-use, 30-minute token. Authenticated session required.
              </p>
            </div>
          )}

          {state.status === 'awaiting_reason' && (
            <div>
              <h2 className="text-xl font-semibold text-slate-900">
                Reject privileged-access request
              </h2>
              <p className="text-sm text-slate-600 mt-2">
                A short reason will be written to the evidence chain alongside the rejection. Both the technician and the
                internal security audit will see this.
              </p>
              <textarea
                value={rejectionReason}
                onChange={(e) => setRejectionReason(e.target.value)}
                rows={4}
                maxLength={1000}
                placeholder="e.g. Not aware of any maintenance window — please confirm with our site lead first."
                className="mt-4 w-full px-4 py-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
              />
              <div className="flex items-center justify-between mt-4">
                <span className="text-xs text-slate-400">{rejectionReason.length} / 1000</span>
                <button
                  onClick={() => void consume(rejectionReason.trim() || undefined)}
                  disabled={rejectionReason.trim().length < 5}
                  className="px-4 py-2 bg-red-600 text-white font-medium rounded-lg hover:bg-red-700 disabled:opacity-50"
                >
                  Submit Rejection
                </button>
              </div>
            </div>
          )}

          {state.status === 'success' && (
            <div className="text-center">
              <div className={`w-12 h-12 mx-auto rounded-full flex items-center justify-center mb-4 ${
                action === 'approve' ? 'bg-emerald-100 text-emerald-600' : 'bg-amber-100 text-amber-700'
              }`}>
                <span className="text-2xl font-bold">{action === 'approve' ? '✓' : '✗'}</span>
              </div>
              <h2 className="text-xl font-semibold text-slate-900">{state.message}</h2>
              <p className="text-sm text-slate-600 mt-2">{state.detail}</p>
              {state.request_id && (
                <p className="text-xs text-slate-400 mt-4 font-mono">
                  Request ID: {state.request_id}
                </p>
              )}
              <p className="text-xs text-slate-400 mt-6">
                You can close this tab.
              </p>
            </div>
          )}

          {(state.status === 'unauthenticated' ||
            state.status === 'forbidden' ||
            state.status === 'error') && (
            <div>
              <h2 className="text-lg font-semibold text-slate-900">{state.message}</h2>
              <p className="text-sm text-slate-600 mt-2">{state.detail}</p>
              {state.status === 'unauthenticated' && (
                <p className="text-xs text-slate-400 mt-4">
                  Why? The link proves WHICH approver was emailed. Your logged-in session proves WHO is acting. Both are
                  required so the attestation chain identifies the actual person — not just whoever forwarded the email.
                </p>
              )}
            </div>
          )}
        </div>

        <p className="mt-8 text-center text-xs text-slate-400 max-w-md mx-auto">
          Every approval and rejection is Ed25519-signed, hash-chained into your site's evidence record, and anchored to
          Bitcoin via OpenTimestamps. {' '}
          <a href={`mailto:${BRANDING.support_email}`} className="text-blue-600 hover:underline">
            Contact support
          </a>{' '}
          if this link looks unexpected.
        </p>
      </div>
    </div>
  );
};

export default PrivilegedAccessAct;

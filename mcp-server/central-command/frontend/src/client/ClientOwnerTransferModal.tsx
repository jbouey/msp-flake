/**
 * ClientOwnerTransferModal — task #18 phase 1.
 *
 * UI surface for the client_org_owner_transfer state machine
 * (backend: client_owner_transfer.py, migration 273). Pre-Stage-#18
 * the entire state machine + Ed25519 attestation chain shipped with
 * NO UI — clients had to curl the API to transfer org ownership.
 *
 * State flow (matches backend constants):
 *   initial            → caller types target email + reason ≥20ch
 *   POST /initiate     → server creates row in 'pending_current_ack'
 *   pending_current_ack → modal shows re-ack form (CONFIRM-OWNER-TRANSFER)
 *   POST /{id}/ack     → server moves row to 'pending_target_accept',
 *                        emails magic link to target_email
 *   pending_target_accept → modal shows "magic link sent" + cancel option
 *   POST /{id}/cancel  → caller bails out (cancel_reason ≥20ch)
 *   completed          → terminal, target now owns the org (target
 *                        clicks the email link; this modal doesn't
 *                        directly handle the accept)
 *
 * Steve M2 (round-table session 216): the magic-link path itself goes
 * to target_email, so target acceptance happens OUTSIDE this modal.
 * The modal is for the INITIATING owner only.
 *
 * Posture: owner-only (parent ClientSettings already gates
 * canManageUsers). Component re-checks server-side via 403 fall-through.
 */
import React, { useEffect, useState } from 'react';
import { getJson, postJson } from '../utils/portalFetch';

interface OwnerTransferState {
  id: string;
  status:
    | 'pending_current_ack'
    | 'pending_target_accept'
    | 'completed'
    | 'canceled'
    | 'expired';
  target_email: string;
  initiated_at: string;
  ack_at?: string;
  expires_at?: string;
  cooling_off_until?: string;
  reason?: string;
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  // Called when the transfer reaches a terminal state (completed /
  // canceled / expired) so the parent can refresh user lists.
  onResolved?: () => void;
}

const MIN_REASON_CHARS = 20;
const CONFIRM_PHRASE = 'CONFIRM-OWNER-TRANSFER';

const REASON_HELPER =
  `Reason ≥${MIN_REASON_CHARS} chars (audit ledger requires elaboration).`;

export const ClientOwnerTransferModal: React.FC<Props> = ({
  isOpen,
  onClose,
  onResolved,
}) => {
  const [transfer, setTransfer] = useState<OwnerTransferState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Initiate-form state
  const [targetEmail, setTargetEmail] = useState('');
  const [reason, setReason] = useState('');

  // Ack-form state
  const [confirmPhrase, setConfirmPhrase] = useState('');

  // Cancel-form state
  const [cancelReason, setCancelReason] = useState('');
  const [showCancel, setShowCancel] = useState(false);

  // Reset on open/close. Status check on open in case there's an
  // existing in-flight transfer (server idempotency rule: at most
  // one pending per org via the unique partial index in mig 273).
  useEffect(() => {
    if (!isOpen) {
      setTransfer(null);
      setLoading(false);
      setError(null);
      setTargetEmail('');
      setReason('');
      setConfirmPhrase('');
      setCancelReason('');
      setShowCancel(false);
      return;
    }
    void fetchActiveTransfer();
  }, [isOpen]);

  const fetchActiveTransfer = async () => {
    setLoading(true);
    setError(null);
    try {
      // GET /api/client/users/owner-transfer/active resolves the
      // current pending row (if any). 404 = no active transfer.
      const res = await getJson<OwnerTransferState>(
        '/api/client/users/owner-transfer/active',
      );
      setTransfer(res);
    } catch (e) {
      // 404 is the happy path (no active transfer); other errors
      // surface to the user.
      const msg = e instanceof Error ? e.message : String(e);
      if (!msg.includes('404')) setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleInitiate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (reason.length < MIN_REASON_CHARS) {
      setError(`Reason must be at least ${MIN_REASON_CHARS} characters.`);
      return;
    }
    setLoading(true);
    try {
      const created = await postJson<OwnerTransferState>(
        '/api/client/users/owner-transfer/initiate',
        { target_email: targetEmail, reason },
      );
      setTransfer(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to initiate transfer');
    } finally {
      setLoading(false);
    }
  };

  const handleAck = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (confirmPhrase !== CONFIRM_PHRASE) {
      setError(`Type exactly ${CONFIRM_PHRASE} to confirm.`);
      return;
    }
    if (!transfer) return;
    setLoading(true);
    try {
      const updated = await postJson<OwnerTransferState>(
        `/api/client/users/owner-transfer/${encodeURIComponent(transfer.id)}/ack`,
        { confirm_phrase: confirmPhrase },
      );
      setTransfer(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to confirm');
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (cancelReason.length < MIN_REASON_CHARS) {
      setError(`Cancel reason must be at least ${MIN_REASON_CHARS} characters.`);
      return;
    }
    if (!transfer) return;
    setLoading(true);
    try {
      const updated = await postJson<OwnerTransferState>(
        `/api/client/users/owner-transfer/${encodeURIComponent(transfer.id)}/cancel`,
        { cancel_reason: cancelReason },
      );
      setTransfer(updated);
      onResolved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to cancel transfer');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between mb-4">
          <h2 className="text-lg font-semibold text-slate-900">
            Transfer Owner Role
          </h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {error && (
          <div className="mb-4 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
            {error}
          </div>
        )}

        {loading && !transfer && (
          <div className="py-8 text-center">
            <div className="w-6 h-6 border-2 border-teal-500 border-t-transparent rounded-full animate-spin mx-auto" />
          </div>
        )}

        {/* No active transfer → initiate form */}
        {!loading && !transfer && (
          <form onSubmit={handleInitiate} className="space-y-4">
            <p className="text-sm text-slate-600">
              Transfer the org-owner role to another user in your organization.
              Both parties must confirm. The change is cryptographically attested
              and visible in your auditor kit.
            </p>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                New owner's email
              </label>
              <input
                type="email"
                value={targetEmail}
                onChange={(e) => setTargetEmail(e.target.value)}
                required
                placeholder="newowner@example.com"
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none"
              />
              <p className="mt-1 text-xs text-slate-500">
                If they're not yet a user in your organization, they'll be
                invited as part of the transfer flow.
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Reason
              </label>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
                rows={3}
                placeholder="e.g. Practice ownership transferred to new clinical director on 2026-06-01"
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none resize-none"
              />
              <p className="mt-1 text-xs text-slate-500">
                {REASON_HELPER} {reason.length}/{MIN_REASON_CHARS}
              </p>
            </div>
            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={onClose}
                className="flex-1 px-4 py-2 text-sm rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={loading || reason.length < MIN_REASON_CHARS}
                className="flex-1 px-4 py-2 text-sm rounded-lg text-white hover:brightness-110 disabled:opacity-50"
                style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}
              >
                {loading ? 'Initiating…' : 'Initiate transfer'}
              </button>
            </div>
          </form>
        )}

        {/* pending_current_ack → re-confirm */}
        {transfer && transfer.status === 'pending_current_ack' && !showCancel && (
          <form onSubmit={handleAck} className="space-y-4">
            <div className="px-3 py-2 rounded-lg bg-yellow-50 border border-yellow-200 text-sm text-yellow-800">
              <p className="font-medium">Confirm transfer initiation</p>
              <p className="mt-1">
                Re-typing the confirmation phrase closes the
                "compromised-session click-through" attack — the same
                human who typed the reason confirms intent.
              </p>
            </div>
            <div className="text-sm text-slate-700">
              <p><span className="text-slate-500">Target:</span> <span className="font-medium">{transfer.target_email}</span></p>
              {transfer.reason && (
                <p className="mt-1"><span className="text-slate-500">Reason:</span> {transfer.reason}</p>
              )}
              <p className="mt-1 text-xs text-slate-500">
                Initiated {new Date(transfer.initiated_at).toLocaleString()}
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Type the phrase to confirm
              </label>
              <input
                type="text"
                value={confirmPhrase}
                onChange={(e) => setConfirmPhrase(e.target.value)}
                required
                placeholder={CONFIRM_PHRASE}
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg font-mono focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none"
                autoComplete="off"
              />
              <p className="mt-1 text-xs text-slate-500 font-mono">{CONFIRM_PHRASE}</p>
            </div>
            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowCancel(true)}
                className="px-4 py-2 text-sm rounded-lg bg-red-50 text-red-700 hover:bg-red-100"
              >
                Cancel transfer instead
              </button>
              <button
                type="submit"
                disabled={loading || confirmPhrase !== CONFIRM_PHRASE}
                className="flex-1 px-4 py-2 text-sm rounded-lg text-white hover:brightness-110 disabled:opacity-50"
                style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}
              >
                {loading ? 'Confirming…' : 'Confirm + send magic link'}
              </button>
            </div>
          </form>
        )}

        {/* pending_target_accept → magic link sent */}
        {transfer && transfer.status === 'pending_target_accept' && !showCancel && (
          <div className="space-y-4">
            <div className="px-3 py-2 rounded-lg bg-blue-50 border border-blue-200 text-sm text-blue-800">
              <p className="font-medium">Magic link sent</p>
              <p className="mt-1">
                We emailed a single-use acceptance link to{' '}
                <span className="font-medium">{transfer.target_email}</span>.
                The transfer completes when they click it.
              </p>
            </div>
            <div className="text-sm text-slate-700 space-y-1">
              {transfer.cooling_off_until && (
                <p><span className="text-slate-500">Cooling-off ends:</span> {new Date(transfer.cooling_off_until).toLocaleString()}</p>
              )}
              {transfer.expires_at && (
                <p><span className="text-slate-500">Link expires:</span> {new Date(transfer.expires_at).toLocaleString()}</p>
              )}
            </div>
            <div className="pt-2 flex gap-2">
              <button
                onClick={onClose}
                className="flex-1 px-4 py-2 text-sm rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
              >
                Close
              </button>
              <button
                onClick={() => setShowCancel(true)}
                className="px-4 py-2 text-sm rounded-lg bg-red-50 text-red-700 hover:bg-red-100"
              >
                Cancel transfer
              </button>
            </div>
          </div>
        )}

        {/* Cancel form (overrides current state) */}
        {transfer && showCancel && (
          <form onSubmit={handleCancel} className="space-y-4">
            <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-800">
              <p className="font-medium">Cancel pending transfer</p>
              <p className="mt-1">
                Canceling writes a cryptographically attested entry to
                your auditor kit. Provide a reason (≥{MIN_REASON_CHARS} chars).
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Cancel reason
              </label>
              <textarea
                value={cancelReason}
                onChange={(e) => setCancelReason(e.target.value)}
                required
                rows={3}
                placeholder="e.g. Wrong target user — re-initiating with corrected email"
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-red-500/40 outline-none resize-none"
              />
              <p className="mt-1 text-xs text-slate-500">
                {cancelReason.length}/{MIN_REASON_CHARS}
              </p>
            </div>
            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowCancel(false)}
                className="flex-1 px-4 py-2 text-sm rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
              >
                Back
              </button>
              <button
                type="submit"
                disabled={loading || cancelReason.length < MIN_REASON_CHARS}
                className="flex-1 px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >
                {loading ? 'Canceling…' : 'Cancel transfer'}
              </button>
            </div>
          </form>
        )}

        {/* Terminal — completed / canceled / expired */}
        {transfer &&
          (transfer.status === 'completed' ||
            transfer.status === 'canceled' ||
            transfer.status === 'expired') && (
            <div className="space-y-4">
              <div
                className={`px-3 py-2 rounded-lg text-sm ${
                  transfer.status === 'completed'
                    ? 'bg-green-50 border border-green-200 text-green-800'
                    : 'bg-slate-50 border border-slate-200 text-slate-700'
                }`}
              >
                <p className="font-medium capitalize">Transfer {transfer.status}</p>
                <p className="mt-1">
                  Target: <span className="font-medium">{transfer.target_email}</span>
                </p>
              </div>
              <button
                onClick={onClose}
                className="w-full px-4 py-2 text-sm rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
              >
                Close
              </button>
            </div>
          )}
      </div>
    </div>
  );
};

export default ClientOwnerTransferModal;

/**
 * PartnerAdminTransferModal — task #18 phase 2.
 *
 * UI for the partner_admin_transfer state machine (backend:
 * partner_admin_transfer.py, migration 274). Operator-class shape per
 * Maya's round-table 2026-05-04 verdict — simpler than the client
 * owner-transfer:
 *   - 2-state machine (pending_target_accept → completed)
 *   - NO cooling-off
 *   - NO magic-link to NEW mailbox (target must already be a
 *     partner_user with role!=admin in the same partner_org)
 *   - Target accepts via the same partner portal session (not email)
 *
 * The initiator types CONFIRM-PARTNER-ADMIN-TRANSFER + ≥20ch reason;
 * the (currently-logged-in) target types ACCEPT-PARTNER-ADMIN to take
 * the role. Cancel takes ≥20ch cancel_reason.
 *
 * Each transition writes an Ed25519 attestation bundle (4 events:
 * initiated, completed, canceled, expired). NOT in PRIVILEGED_ORDER_TYPES
 * (admin-API class, not fleet_orders).
 */
import React, { useEffect, useState } from 'react';
import { getJson, postJson } from '../utils/portalFetch';

interface PartnerAdminTransferState {
  id: string;
  status:
    | 'pending_target_accept'
    | 'completed'
    | 'canceled'
    | 'expired';
  target_email: string;
  initiated_at: string;
  expires_at?: string;
  reason?: string;
  initiator_id?: string;
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  // Caller's partner role — drives whether the action button is enabled.
  // Only `admin` can initiate / accept; other roles get a read-only
  // view of any in-flight transfer.
  partnerRole?: string;
  // Caller's user_id — used to hide the "Accept" form for the initiator
  // (target must be a different partner_user).
  callerUserId?: string;
  onResolved?: () => void;
}

const MIN_REASON_CHARS = 20;
const INITIATE_PHRASE = 'CONFIRM-PARTNER-ADMIN-TRANSFER';
const ACCEPT_PHRASE = 'ACCEPT-PARTNER-ADMIN';

export const PartnerAdminTransferModal: React.FC<Props> = ({
  isOpen,
  onClose,
  partnerRole,
  callerUserId,
  onResolved,
}) => {
  const [transfer, setTransfer] = useState<PartnerAdminTransferState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Initiate-form state
  const [targetEmail, setTargetEmail] = useState('');
  const [reason, setReason] = useState('');
  const [initiateConfirm, setInitiateConfirm] = useState('');

  // Accept-form state
  const [acceptConfirm, setAcceptConfirm] = useState('');

  // Cancel-form state
  const [cancelReason, setCancelReason] = useState('');
  const [showCancel, setShowCancel] = useState(false);

  const isAdmin = partnerRole === 'admin';
  const isInitiator =
    transfer && callerUserId && transfer.initiator_id === callerUserId;

  useEffect(() => {
    if (!isOpen) {
      setTransfer(null);
      setLoading(false);
      setError(null);
      setTargetEmail('');
      setReason('');
      setInitiateConfirm('');
      setAcceptConfirm('');
      setCancelReason('');
      setShowCancel(false);
      return;
    }
    void fetchActive();
  }, [isOpen]);

  const fetchActive = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getJson<PartnerAdminTransferState>(
        '/api/partners/me/admin-transfer/active',
      );
      setTransfer(res);
    } catch (e) {
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
    if (initiateConfirm !== INITIATE_PHRASE) {
      setError(`Type exactly ${INITIATE_PHRASE} to initiate.`);
      return;
    }
    setLoading(true);
    try {
      const created = await postJson<PartnerAdminTransferState>(
        '/api/partners/me/admin-transfer/initiate',
        {
          target_email: targetEmail,
          reason,
          confirm_phrase: initiateConfirm,
        },
      );
      setTransfer(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to initiate transfer');
    } finally {
      setLoading(false);
    }
  };

  const handleAccept = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (acceptConfirm !== ACCEPT_PHRASE) {
      setError(`Type exactly ${ACCEPT_PHRASE} to accept.`);
      return;
    }
    if (!transfer) return;
    setLoading(true);
    try {
      const updated = await postJson<PartnerAdminTransferState>(
        `/api/partners/me/admin-transfer/${encodeURIComponent(transfer.id)}/accept`,
        { confirm_phrase: acceptConfirm },
      );
      setTransfer(updated);
      onResolved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to accept transfer');
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
      const updated = await postJson<PartnerAdminTransferState>(
        `/api/partners/me/admin-transfer/${encodeURIComponent(transfer.id)}/cancel`,
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
            Transfer Partner Admin Role
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

        {/* No active transfer → initiate (admin-only) */}
        {!loading && !transfer && (
          isAdmin ? (
            <form onSubmit={handleInitiate} className="space-y-4">
              <p className="text-sm text-slate-600">
                Transfer the partner admin role to another existing user
                in your partner organization. The target user must already
                be a partner_user (currently with role tech or billing) —
                this flow does NOT create new accounts. Target accepts
                from their own logged-in session; no email magic link.
              </p>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Target user's email (must be an existing partner_user)
                </label>
                <input
                  type="email"
                  value={targetEmail}
                  onChange={(e) => setTargetEmail(e.target.value)}
                  required
                  placeholder="newadmin@partner.com"
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none"
                />
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
                  placeholder="e.g. Stepping back from day-to-day; transferring admin to senior tech who runs ops"
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none resize-none"
                />
                <p className="mt-1 text-xs text-slate-500">
                  ≥{MIN_REASON_CHARS} chars · {reason.length}/{MIN_REASON_CHARS}
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Confirmation phrase
                </label>
                <input
                  type="text"
                  value={initiateConfirm}
                  onChange={(e) => setInitiateConfirm(e.target.value)}
                  required
                  placeholder={INITIATE_PHRASE}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg font-mono text-sm focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none"
                  autoComplete="off"
                />
                <p className="mt-1 text-xs text-slate-500 font-mono">{INITIATE_PHRASE}</p>
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
                  disabled={
                    loading ||
                    reason.length < MIN_REASON_CHARS ||
                    initiateConfirm !== INITIATE_PHRASE
                  }
                  className="flex-1 px-4 py-2 text-sm rounded-lg text-white hover:brightness-110 disabled:opacity-50"
                  style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}
                >
                  {loading ? 'Initiating…' : 'Initiate transfer'}
                </button>
              </div>
            </form>
          ) : (
            <div className="space-y-4">
              <div className="px-3 py-2 rounded-lg bg-slate-50 border border-slate-200 text-sm text-slate-700">
                <p className="font-medium">No active transfer</p>
                <p className="mt-1">
                  Only the partner admin can initiate an admin-role
                  transfer. Your role: {partnerRole ?? 'unknown'}.
                </p>
              </div>
              <button
                onClick={onClose}
                className="w-full px-4 py-2 text-sm rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
              >
                Close
              </button>
            </div>
          )
        )}

        {/* pending_target_accept */}
        {transfer && transfer.status === 'pending_target_accept' && !showCancel && (
          <div className="space-y-4">
            <div className="px-3 py-2 rounded-lg bg-yellow-50 border border-yellow-200 text-sm text-yellow-800">
              <p className="font-medium">Transfer awaiting target acceptance</p>
              <p className="mt-1">
                Target: <span className="font-medium">{transfer.target_email}</span>
              </p>
              {transfer.expires_at && (
                <p className="mt-1">
                  Expires: {new Date(transfer.expires_at).toLocaleString()}
                </p>
              )}
              {transfer.reason && (
                <p className="mt-2 text-yellow-700">
                  <span className="text-yellow-600">Reason:</span> {transfer.reason}
                </p>
              )}
            </div>

            {/* If caller is the target (not the initiator), show accept form */}
            {!isInitiator && (
              <form onSubmit={handleAccept} className="space-y-3 border-t border-slate-100 pt-4">
                <p className="text-sm text-slate-700">
                  If you are the target ({transfer.target_email}) and want
                  to accept the admin role, type the phrase below.
                  Acceptance is immediate — no cooling-off.
                </p>
                <input
                  type="text"
                  value={acceptConfirm}
                  onChange={(e) => setAcceptConfirm(e.target.value)}
                  required
                  placeholder={ACCEPT_PHRASE}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg font-mono text-sm focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none"
                  autoComplete="off"
                />
                <p className="text-xs text-slate-500 font-mono">{ACCEPT_PHRASE}</p>
                <button
                  type="submit"
                  disabled={loading || acceptConfirm !== ACCEPT_PHRASE}
                  className="w-full px-4 py-2 text-sm rounded-lg text-white hover:brightness-110 disabled:opacity-50"
                  style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}
                >
                  {loading ? 'Accepting…' : 'Accept admin role'}
                </button>
              </form>
            )}

            <div className="flex gap-2 pt-2">
              <button
                onClick={onClose}
                className="flex-1 px-4 py-2 text-sm rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
              >
                Close
              </button>
              {(isAdmin || isInitiator) && (
                <button
                  onClick={() => setShowCancel(true)}
                  className="px-4 py-2 text-sm rounded-lg bg-red-50 text-red-700 hover:bg-red-100"
                >
                  Cancel transfer
                </button>
              )}
            </div>
          </div>
        )}

        {/* Cancel form */}
        {transfer && showCancel && (
          <form onSubmit={handleCancel} className="space-y-4">
            <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-800">
              <p className="font-medium">Cancel pending transfer</p>
              <p className="mt-1">
                Canceling writes a cryptographically attested entry to
                your partner_org's auditor kit. Provide a reason
                (≥{MIN_REASON_CHARS} chars).
              </p>
            </div>
            <textarea
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              required
              rows={3}
              placeholder="e.g. Wrong target — re-initiating with corrected user"
              className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-red-500/40 outline-none resize-none"
            />
            <p className="text-xs text-slate-500">
              {cancelReason.length}/{MIN_REASON_CHARS}
            </p>
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

        {/* Terminal */}
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

export default PartnerAdminTransferModal;

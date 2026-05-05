/**
 * AdminClientUserEmailRenameModal — task #18 phase 4.
 *
 * Substrate-side client_user email rename UI. Calls the existing #23
 * substrate endpoint
 *   POST /api/admin/client-users/{user_id}/change-email
 * which requires:
 *   - auth: admin_user session (require_auth)
 *   - reason ≥40 chars (higher friction than partner side per Steve)
 *   - confirm_phrase = exact literal "SUBSTRATE-CLIENT-EMAIL-CHANGE"
 *
 * Posture (round-table 22, 2026-05-05): substrate rename is for
 * recovery cases when the partner can't act (e.g. testing-org with no
 * real owner email). Each transition writes Ed25519 attestation;
 * partner gets P0 op-alert if the target is owner-role, P1 otherwise.
 *
 * Pre-Phase-4 the only entry was direct curl. North Valley 2026-05-05
 * was renamed via raw SQL because no UI existed; this closes that gap.
 */
import React, { useState } from 'react';
import { csrfHeaders } from '../utils/csrf';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  userId: string;
  currentEmail: string;
  role: string;
  orgName: string;
  onResolved?: () => void;
}

const MIN_REASON_CHARS = 40;
const CONFIRM_PHRASE = 'SUBSTRATE-CLIENT-EMAIL-CHANGE';

export const AdminClientUserEmailRenameModal: React.FC<Props> = ({
  isOpen,
  onClose,
  userId,
  currentEmail,
  role,
  orgName,
  onResolved,
}) => {
  const [newEmail, setNewEmail] = useState('');
  const [reason, setReason] = useState('');
  const [confirmPhrase, setConfirmPhrase] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{
    new_email: string;
    attestation_bundle_id: string | null;
  } | null>(null);

  const reset = () => {
    setNewEmail('');
    setReason('');
    setConfirmPhrase('');
    setLoading(false);
    setError(null);
    setResult(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newEmail.trim().toLowerCase() === currentEmail.trim().toLowerCase()) {
      setError('New email matches current — nothing to change.');
      return;
    }
    if (reason.length < MIN_REASON_CHARS) {
      setError(`Reason must be at least ${MIN_REASON_CHARS} characters.`);
      return;
    }
    if (confirmPhrase !== CONFIRM_PHRASE) {
      setError(`Type exactly ${CONFIRM_PHRASE} to confirm.`);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(
        `/api/admin/client-users/${encodeURIComponent(userId)}/change-email`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
          body: JSON.stringify({
            new_email: newEmail,
            reason,
            confirm_phrase: confirmPhrase,
          }),
        },
      );
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        let parsed: { detail?: string } | undefined;
        try {
          parsed = JSON.parse(text);
        } catch {
          // not JSON
        }
        throw new Error(parsed?.detail || `${res.status} ${text || res.statusText}`);
      }
      const data = await res.json();
      setResult({
        new_email: data.new_email,
        attestation_bundle_id: data.attestation_bundle_id,
      });
      onResolved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Rename failed');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto border-2 border-red-200">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">
              Substrate Email Rename
            </h2>
            <p className="text-xs text-red-600 mt-1 font-medium uppercase tracking-wider">
              Substrate-class action
            </p>
          </div>
          <button
            onClick={() => { reset(); onClose(); }}
            className="text-slate-400 hover:text-slate-600"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-900">
          <p className="font-medium">You are acting as substrate, not operator.</p>
          <p className="mt-1">
            This is identity-recovery on a substrate-managed table. The
            partner will receive a {role === 'owner' ? 'P0' : 'P1'} alert.
            Use only when the partner cannot act through their own portal.
          </p>
        </div>

        {error && (
          <div className="mb-4 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
            {error}
          </div>
        )}

        {result ? (
          <div className="space-y-4">
            <div className="px-3 py-2 rounded-lg bg-green-50 border border-green-200 text-sm text-green-800">
              <p className="font-medium">Email rename completed</p>
              <p className="mt-1">
                {currentEmail} → <span className="font-mono">{result.new_email}</span>
              </p>
              {result.attestation_bundle_id && (
                <p className="mt-1 text-xs">
                  Attestation bundle: <span className="font-mono">{result.attestation_bundle_id}</span>
                </p>
              )}
            </div>
            <button
              onClick={() => { reset(); onClose(); }}
              className="w-full px-4 py-2 text-sm rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
            >
              Close
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="text-sm text-slate-700 space-y-1">
              <p><span className="text-slate-500">Org:</span> <span className="font-medium">{orgName}</span></p>
              <p><span className="text-slate-500">Current:</span> <span className="font-mono">{currentEmail}</span></p>
              <p><span className="text-slate-500">Role:</span> <span className="font-medium capitalize">{role}</span></p>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                New email
              </label>
              <input
                type="email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                required
                placeholder="newowner@example.com"
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-red-500/40 focus:border-red-300 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Reason (substrate friction — ≥{MIN_REASON_CHARS} chars)
              </label>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
                rows={4}
                placeholder="e.g. Partner unable to access target customer's email; substrate rename required to restore portal access for HIPAA-mandated breach-response role assignment."
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-red-500/40 focus:border-red-300 outline-none resize-none"
              />
              <p className="mt-1 text-xs text-slate-500">
                {reason.length}/{MIN_REASON_CHARS}
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Confirmation phrase
              </label>
              <input
                type="text"
                value={confirmPhrase}
                onChange={(e) => setConfirmPhrase(e.target.value)}
                required
                placeholder={CONFIRM_PHRASE}
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg font-mono text-sm focus:ring-2 focus:ring-red-500/40 focus:border-red-300 outline-none"
                autoComplete="off"
              />
              <p className="mt-1 text-xs text-slate-500 font-mono">{CONFIRM_PHRASE}</p>
            </div>
            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={() => { reset(); onClose(); }}
                className="flex-1 px-4 py-2 text-sm rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={
                  loading ||
                  reason.length < MIN_REASON_CHARS ||
                  confirmPhrase !== CONFIRM_PHRASE
                }
                className="flex-1 px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >
                {loading ? 'Renaming…' : 'Rename email'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
};

export default AdminClientUserEmailRenameModal;

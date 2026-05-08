/**
 * PartnerUsersScreen — task #18 phase 3.
 *
 * Read-only listing of partner_users in the caller's partner_org +
 * entry point for the partner admin-transfer modal (mig 274). Pre-
 * phase-3 there was no partner-side users surface at all; the only
 * write paths were the operator-side POST /api/partners/{partner_id}/users
 * (admin-class create) and POST /{partner_id}/users/{user_id}/magic-link
 * — neither had self-service UI.
 *
 * Scope intentionally tight for v1: list + role/status/MFA badges +
 * admin-transfer entry point. Invite / role-change / deactivate land
 * in a follow-up once the corresponding self-scoped backend endpoints
 * exist (current /{partner_id}/users routes are operator-class admin
 * paths, not partner self-service).
 */
import React, { useEffect, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { usePartner } from './PartnerContext';
import { postJson, patchJson, deleteJson } from '../utils/portalFetch';
import type { PortalFetchError } from '../utils/portalFetch';
import { PartnerAdminTransferModal } from './PartnerAdminTransferModal';
import { DangerousActionModal } from '../components/composed';

const DEACTIVATE_PHRASE = 'DEACTIVATE-PARTNER-USER';

interface PartnerUser {
  id: string;
  email: string;
  name: string | null;
  role: 'admin' | 'tech' | 'billing' | string;
  status: string;
  mfa_enabled: boolean;
  mfa_required: boolean;
  last_login_at: string | null;
  created_at: string | null;
}

interface UsersResponse {
  users: PartnerUser[];
  count: number;
}

const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin',
  tech: 'Tech',
  billing: 'Billing',
};

const ROLE_BADGES: Record<string, string> = {
  admin: 'bg-indigo-100 text-indigo-700',
  tech: 'bg-teal-100 text-teal-700',
  billing: 'bg-amber-100 text-amber-700',
};

export const PartnerUsersScreen: React.FC = () => {
  const navigate = useNavigate();
  const { partner, isAuthenticated, isLoading } = usePartner();

  const [users, setUsers] = useState<PartnerUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAdminTransfer, setShowAdminTransfer] = useState(false);
  const [actionBusy, setActionBusy] = useState<string | null>(null);

  // Invite form state
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteName, setInviteName] = useState('');
  const [inviteRole, setInviteRole] = useState<'tech' | 'billing'>('tech');
  const [inviteConfirmOpen, setInviteConfirmOpen] = useState(false);
  const [inviteConfirmError, setInviteConfirmError] = useState<
    string | undefined
  >(undefined);

  // Deactivate confirm state — replaces 2× window.prompt with the
  // DangerousActionModal tier-1 typed-confirm gate.
  const [deactivateUser, setDeactivateUser] = useState<PartnerUser | null>(null);
  const [deactivateReason, setDeactivateReason] = useState('');
  const [deactivateError, setDeactivateError] = useState<string | undefined>(
    undefined,
  );
  const [deactivateBusy, setDeactivateBusy] = useState(false);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/partner/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  useEffect(() => {
    if (isAuthenticated) {
      void fetchUsers();
    }
  }, [isAuthenticated]);

  const fetchUsers = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/partners/me/users', {
        credentials: 'include',
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        throw new Error(`${res.status} ${detail || res.statusText}`);
      }
      const data: UsersResponse = await res.json();
      setUsers(data.users || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load users');
    } finally {
      setLoading(false);
    }
  };

  const _setErrorFromException = (e: unknown, fallback: string) => {
    const err = e as PortalFetchError;
    setError(err.detail || err.message || fallback);
  };

  const handleInvite = (e: React.FormEvent) => {
    // Form submit now opens the tier-2 DangerousActionModal so the user
    // sees an explicit "Invite {email}?" confirmation before the POST.
    e.preventDefault();
    if (!inviteEmail) return;
    setError(null);
    setInviteConfirmError(undefined);
    setInviteConfirmOpen(true);
  };

  const performInvite = async () => {
    setInviteConfirmError(undefined);
    setActionBusy('invite');
    try {
      await postJson('/api/partners/me/users', {
        email: inviteEmail,
        name: inviteName || null,
        role: inviteRole,
      });
      setInviteConfirmOpen(false);
      setInviteOpen(false);
      setInviteEmail('');
      setInviteName('');
      setInviteRole('tech');
      await fetchUsers();
    } catch (e) {
      const err = e as PortalFetchError;
      setInviteConfirmError(err.detail || err.message || 'Invite failed.');
    } finally {
      setActionBusy(null);
    }
  };

  const handleRoleChange = async (u: PartnerUser, nextRole: string) => {
    if (nextRole === u.role) return;
    const reason = window.prompt(
      `Reason for changing ${u.email} from ${u.role} to ${nextRole} (≥20 chars, audit ledger):`,
      '',
    );
    if (reason === null) return;
    if (reason.length < 20) {
      setError('Reason must be at least 20 characters.');
      return;
    }
    setError(null);
    setActionBusy(u.id);
    try {
      await patchJson(
        `/api/partners/me/users/${encodeURIComponent(u.id)}/role`,
        { role: nextRole, reason },
      );
      await fetchUsers();
    } catch (e) {
      _setErrorFromException(e, 'Role change failed');
    } finally {
      setActionBusy(null);
    }
  };

  const openDeactivate = (u: PartnerUser) => {
    setDeactivateUser(u);
    setDeactivateReason('');
    setDeactivateError(undefined);
  };

  const closeDeactivate = () => {
    if (deactivateBusy) return;
    setDeactivateUser(null);
    setDeactivateReason('');
    setDeactivateError(undefined);
  };

  const performDeactivate = async () => {
    if (!deactivateUser) return;
    if (deactivateReason.trim().length < 20) {
      setDeactivateError('Reason must be at least 20 characters.');
      return;
    }
    setDeactivateError(undefined);
    setDeactivateBusy(true);
    setActionBusy(deactivateUser.id);
    try {
      await deleteJson(
        `/api/partners/me/users/${encodeURIComponent(deactivateUser.id)}`,
        {
          reason: deactivateReason.trim(),
          confirm_phrase: DEACTIVATE_PHRASE,
        },
      );
      setDeactivateUser(null);
      setDeactivateReason('');
      await fetchUsers();
    } catch (e) {
      const err = e as PortalFetchError;
      setDeactivateError(
        err.detail || err.message || 'Deactivation failed.',
      );
    } finally {
      setDeactivateBusy(false);
      setActionBusy(null);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50/80 flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!partner) return null;

  return (
    <div className="min-h-screen bg-slate-50/80 page-enter">
      <header className="sticky top-0 z-30 border-b border-slate-200/60" style={{ background: 'rgba(255,255,255,0.82)', backdropFilter: 'blur(20px) saturate(180%)', WebkitBackdropFilter: 'blur(20px) saturate(180%)' }}>
        <div className="max-w-5xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link to="/partner/dashboard" className="p-2 text-slate-500 hover:text-indigo-600 rounded-lg hover:bg-indigo-50 transition">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </Link>
            <div>
              <h1 className="text-lg font-semibold text-slate-900 tracking-tight">Partner Users</h1>
              <p className="text-xs text-slate-500">{partner.name}</p>
            </div>
          </div>
          <div className="flex gap-2">
            {/* MAJ-2 fix: client-gate by role so non-admins don't see
                actions that backend will reject. Backend gates remain
                authoritative (require_partner_role("admin")). */}
            {partner?.user_role === 'admin' && (
              <button
                onClick={() => setInviteOpen(true)}
                className="px-4 py-2 text-sm rounded-lg text-white hover:brightness-110"
                style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}
              >
                Invite user
              </button>
            )}
            {partner?.user_role === 'admin' && (
              <button
                onClick={() => setShowAdminTransfer(true)}
                className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50"
              >
                Manage admin transfer
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8">
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
            {error}
          </div>
        )}

        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-900">Team members</h2>
            <span className="text-sm text-slate-500">{users.length} total</span>
          </div>
          {loading ? (
            <div className="p-8 text-center">
              <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin mx-auto" />
            </div>
          ) : users.length === 0 ? (
            <div className="p-8 text-center text-slate-500">
              No partner users yet.
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {users.map((u) => (
                <div key={u.id} className="px-6 py-4 flex items-center justify-between">
                  <div>
                    <p className="font-medium text-slate-900">
                      {u.name || u.email}
                    </p>
                    {u.name && (
                      <p className="text-xs text-slate-500">{u.email}</p>
                    )}
                    <p className="mt-1 text-xs text-slate-500">
                      {u.last_login_at
                        ? `Last login ${new Date(u.last_login_at).toLocaleString()}`
                        : 'Never logged in'}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    {u.mfa_required && !u.mfa_enabled && (
                      <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-amber-100 text-amber-700">
                        MFA pending
                      </span>
                    )}
                    {u.mfa_enabled && (
                      <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-emerald-100 text-emerald-700">
                        MFA on
                      </span>
                    )}
                    {u.status !== 'active' && (
                      <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-slate-200 text-slate-600 capitalize">
                        {u.status}
                      </span>
                    )}
                    {u.status === 'active' && u.role !== 'admin' ? (
                      <select
                        value={u.role}
                        disabled={actionBusy === u.id}
                        onChange={(e) => void handleRoleChange(u, e.target.value)}
                        className="px-2 py-1 text-xs border border-slate-300 rounded"
                      >
                        <option value="tech">Tech</option>
                        <option value="billing">Billing</option>
                        <option value="admin">Admin</option>
                      </select>
                    ) : (
                      <span
                        className={`px-3 py-1 text-xs font-semibold rounded-full ${
                          ROLE_BADGES[u.role] || 'bg-slate-100 text-slate-700'
                        }`}
                      >
                        {ROLE_LABELS[u.role] || u.role}
                      </span>
                    )}
                    {u.status === 'active' && (
                      <button
                        onClick={() => openDeactivate(u)}
                        disabled={actionBusy === u.id}
                        className="text-xs text-red-600 hover:underline disabled:opacity-50"
                        title="Deactivate this user (sets status=inactive; not a hard delete)"
                      >
                        Deactivate
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <p className="mt-6 text-xs text-slate-500">
          Each role-change and deactivate writes a cryptographically
          attested entry to your auditor kit. The 1-admin-min database
          trigger blocks the last-admin demote/deactivate; promote
          another user first, or use the admin-transfer flow.
        </p>
      </main>

      {/* Invite modal */}
      {inviteOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-lg font-semibold text-slate-900">Invite partner user</h3>
              <button
                onClick={() => setInviteOpen(false)}
                className="text-slate-400 hover:text-slate-600"
                aria-label="Close"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <form onSubmit={handleInvite} className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Email</label>
                <input
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  required
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:ring-2 focus:ring-indigo-500/40"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Name (optional)
                </label>
                <input
                  type="text"
                  value={inviteName}
                  onChange={(e) => setInviteName(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:ring-2 focus:ring-indigo-500/40"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Role</label>
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value as 'tech' | 'billing')}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:ring-2 focus:ring-indigo-500/40"
                >
                  <option value="tech">Tech</option>
                  <option value="billing">Billing</option>
                </select>
                <p className="mt-1 text-xs text-slate-500">
                  Admin role is reserved for the admin-transfer flow —
                  prevents accidental zero-admin and click-jacking
                  attempts on a single endpoint.
                </p>
              </div>
              <div className="flex gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setInviteOpen(false)}
                  className="flex-1 px-4 py-2 text-sm rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionBusy === 'invite'}
                  className="flex-1 px-4 py-2 text-sm rounded-lg text-white hover:brightness-110 disabled:opacity-50"
                  style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}
                >
                  {actionBusy === 'invite' ? 'Inviting…' : 'Invite'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <PartnerAdminTransferModal
        isOpen={showAdminTransfer}
        onClose={() => setShowAdminTransfer(false)}
        onResolved={() => fetchUsers()}
        // MAJ-2 fix (audit 2026-05-08): pass caller role + user_id so
        // the modal's defensive isAdmin / isInitiator gates work. Backend
        // require_partner_role("admin") stays authoritative; this is UX
        // defense in depth.
        partnerRole={partner?.user_role || undefined}
        callerUserId={partner?.partner_user_id || undefined}
      />

      {/* Tier-2 confirm — invite. Wraps the existing form submit so the
          user sees an explicit "Invite {email}?" before the POST fires. */}
      <DangerousActionModal
        open={inviteConfirmOpen}
        tier="reversible"
        title="Invite partner user"
        verb="Invite"
        target={inviteEmail || ''}
        description={
          <>
            An invite email will be sent. The invitee gets <b>{inviteRole}</b>
            {' '}access to <b>{partner?.name || 'your partner organization'}</b>
            {' '}once they accept.
          </>
        }
        busy={actionBusy === 'invite'}
        errorMessage={inviteConfirmError}
        onConfirm={performInvite}
        onCancel={() => {
          if (actionBusy !== 'invite') {
            setInviteConfirmOpen(false);
            setInviteConfirmError(undefined);
          }
        }}
      />

      {/* Tier-1 confirm — deactivate. Replaces 2× window.prompt with a
          typed-confirm gate (user must type the user's email). Reason
          textarea remains separate (≥20 chars audit-ledger requirement). */}
      <DangerousActionModal
        open={deactivateUser !== null}
        tier="irreversible"
        title="Deactivate partner user"
        verb="Deactivate"
        target={deactivateUser?.email || ''}
        confirmInput="target"
        description={
          <div className="space-y-3">
            <p>
              This sets <b>{deactivateUser?.email}</b>'s status to{' '}
              <b>inactive</b>. They will be signed out and lose access to the
              partner portal. The 1-admin-min database trigger blocks the
              last-admin deactivate; promote another user first.
            </p>
            <label className="block text-sm">
              <span className="text-slate-700 font-medium">
                Reason ({deactivateReason.trim().length}/20+ chars, audit ledger)
              </span>
              <textarea
                value={deactivateReason}
                onChange={(e) => setDeactivateReason(e.target.value)}
                rows={2}
                disabled={deactivateBusy}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                placeholder="Why are you deactivating this user?"
                minLength={20}
              />
            </label>
          </div>
        }
        busy={deactivateBusy}
        errorMessage={deactivateError}
        onConfirm={performDeactivate}
        onCancel={closeDeactivate}
      />
    </div>
  );
};

export default PartnerUsersScreen;

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
import { PartnerAdminTransferModal } from './PartnerAdminTransferModal';

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
          <button
            onClick={() => setShowAdminTransfer(true)}
            className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            Manage admin transfer
          </button>
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
                    <span
                      className={`px-3 py-1 text-xs font-semibold rounded-full ${
                        ROLE_BADGES[u.role] || 'bg-slate-100 text-slate-700'
                      }`}
                    >
                      {ROLE_LABELS[u.role] || u.role}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <p className="mt-6 text-xs text-slate-500">
          Invite, role-change, and deactivate flows ship in a follow-up
          once the corresponding self-scoped backend endpoints exist.
          Operators with admin-class access can use the operator
          dashboard for those actions in the meantime.
        </p>
      </main>

      <PartnerAdminTransferModal
        isOpen={showAdminTransfer}
        onClose={() => setShowAdminTransfer(false)}
        onResolved={() => fetchUsers()}
      />
    </div>
  );
};

export default PartnerUsersScreen;

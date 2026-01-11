import React, { useState, useEffect } from 'react';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { usersApi, AdminUser, UserInvite } from '../utils/api';

type TabType = 'users' | 'invites';

/**
 * Format date for display
 */
function formatDate(dateString: string | null): string {
  if (!dateString) return 'Never';
  return new Date(dateString).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Role badge component
 */
const RoleBadge: React.FC<{ role: string }> = ({ role }) => {
  const variants: Record<string, 'default' | 'success' | 'warning' | 'error'> = {
    admin: 'error',
    operator: 'warning',
    readonly: 'default',
  };
  return <Badge variant={variants[role] || 'default'}>{role}</Badge>;
};

/**
 * Status badge component
 */
const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const variants: Record<string, 'default' | 'success' | 'warning' | 'error'> = {
    active: 'success',
    disabled: 'error',
    pending: 'warning',
    expired: 'default',
    revoked: 'default',
  };
  return <Badge variant={variants[status] || 'default'}>{status}</Badge>;
};

/**
 * User row component
 */
const UserRow: React.FC<{
  user: AdminUser;
  currentUserId: string;
  onEdit: (user: AdminUser) => void;
  onToggleStatus: (user: AdminUser) => void;
  onDelete: (user: AdminUser) => void;
  onResetPassword: (user: AdminUser) => void;
}> = ({ user, currentUserId, onEdit, onToggleStatus, onDelete, onResetPassword }) => {
  const isSelf = user.id === currentUserId;

  return (
    <tr className="hover:bg-fill-tertiary/50 transition-colors">
      <td className="px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-accent-primary flex items-center justify-center text-white font-bold text-sm">
            {(user.display_name || user.username).charAt(0).toUpperCase()}
          </div>
          <div>
            <p className="font-medium text-label-primary">
              {user.display_name || user.username}
              {isSelf && <span className="text-xs text-label-tertiary ml-2">(you)</span>}
            </p>
            <p className="text-xs text-label-tertiary">{user.username}</p>
          </div>
        </div>
      </td>
      <td className="px-4 py-3 text-sm text-label-secondary">{user.email || '-'}</td>
      <td className="px-4 py-3"><RoleBadge role={user.role} /></td>
      <td className="px-4 py-3"><StatusBadge status={user.status} /></td>
      <td className="px-4 py-3 text-sm text-label-tertiary">{formatDate(user.last_login)}</td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <button
            onClick={() => onEdit(user)}
            className="text-xs text-accent-primary hover:underline"
          >
            Edit
          </button>
          <button
            onClick={() => onResetPassword(user)}
            className="text-xs text-label-secondary hover:text-label-primary"
          >
            Reset Password
          </button>
          {!isSelf && (
            <>
              <button
                onClick={() => onToggleStatus(user)}
                className={`text-xs ${user.status === 'active' ? 'text-yellow-500' : 'text-green-500'} hover:underline`}
              >
                {user.status === 'active' ? 'Disable' : 'Enable'}
              </button>
              <button
                onClick={() => onDelete(user)}
                className="text-xs text-red-500 hover:underline"
              >
                Delete
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
};

/**
 * Invite row component
 */
const InviteRow: React.FC<{
  invite: UserInvite;
  onResend: (invite: UserInvite) => void;
  onRevoke: (invite: UserInvite) => void;
}> = ({ invite, onResend, onRevoke }) => {
  const isExpired = new Date(invite.expires_at) < new Date();

  return (
    <tr className="hover:bg-fill-tertiary/50 transition-colors">
      <td className="px-4 py-3">
        <div>
          <p className="font-medium text-label-primary">{invite.email}</p>
          {invite.display_name && (
            <p className="text-xs text-label-tertiary">{invite.display_name}</p>
          )}
        </div>
      </td>
      <td className="px-4 py-3"><RoleBadge role={invite.role} /></td>
      <td className="px-4 py-3">
        <StatusBadge status={isExpired ? 'expired' : invite.status} />
      </td>
      <td className="px-4 py-3 text-sm text-label-tertiary">
        {invite.invited_by_name || 'Unknown'}
      </td>
      <td className="px-4 py-3 text-sm text-label-tertiary">{formatDate(invite.expires_at)}</td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <button
            onClick={() => onResend(invite)}
            className="text-xs text-accent-primary hover:underline"
          >
            Resend
          </button>
          <button
            onClick={() => onRevoke(invite)}
            className="text-xs text-red-500 hover:underline"
          >
            Revoke
          </button>
        </div>
      </td>
    </tr>
  );
};

/**
 * Invite User Modal
 */
const InviteUserModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { email: string; role: string; display_name?: string }) => void;
  isLoading: boolean;
  error: string | null;
}> = ({ isOpen, onClose, onSubmit, isLoading, error }) => {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'admin' | 'operator' | 'readonly'>('operator');
  const [displayName, setDisplayName] = useState('');

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      email,
      role,
      display_name: displayName || undefined,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <GlassCard className="w-full max-w-md">
        <h2 className="text-xl font-semibold mb-4">Invite User</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Email Address *
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              placeholder="user@example.com"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Display Name
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              placeholder="John Doe"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Role *
            </label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as 'admin' | 'operator' | 'readonly')}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
            >
              <option value="admin">Admin - Full access including user management</option>
              <option value="operator">Operator - View and execute actions</option>
              <option value="readonly">Readonly - View only access</option>
            </select>
          </div>

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-label-secondary hover:text-label-primary transition-colors"
              disabled={isLoading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
              disabled={isLoading || !email}
            >
              {isLoading ? <Spinner size="sm" /> : 'Send Invite'}
            </button>
          </div>
        </form>
      </GlassCard>
    </div>
  );
};

/**
 * Edit User Modal
 */
const EditUserModal: React.FC<{
  user: AdminUser | null;
  onClose: () => void;
  onSubmit: (userId: string, data: { role?: string; display_name?: string }) => void;
  isLoading: boolean;
  error: string | null;
}> = ({ user, onClose, onSubmit, isLoading, error }) => {
  const [role, setRole] = useState<string>(user?.role || 'operator');
  const [displayName, setDisplayName] = useState(user?.display_name || '');

  useEffect(() => {
    if (user) {
      setRole(user.role);
      setDisplayName(user.display_name || '');
    }
  }, [user]);

  if (!user) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(user.id, {
      role: role !== user.role ? role : undefined,
      display_name: displayName !== user.display_name ? displayName : undefined,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <GlassCard className="w-full max-w-md">
        <h2 className="text-xl font-semibold mb-4">Edit User</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Username
            </label>
            <input
              type="text"
              value={user.username}
              disabled
              className="w-full px-3 py-2 bg-fill-tertiary border border-separator-default rounded-lg text-label-tertiary"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Display Name
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Role
            </label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
            >
              <option value="admin">Admin</option>
              <option value="operator">Operator</option>
              <option value="readonly">Readonly</option>
            </select>
          </div>

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-label-secondary hover:text-label-primary transition-colors"
              disabled={isLoading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
              disabled={isLoading}
            >
              {isLoading ? <Spinner size="sm" /> : 'Save Changes'}
            </button>
          </div>
        </form>
      </GlassCard>
    </div>
  );
};

/**
 * Reset Password Modal
 */
const ResetPasswordModal: React.FC<{
  user: AdminUser | null;
  onClose: () => void;
  onSubmit: (userId: string, password: string) => void;
  isLoading: boolean;
  error: string | null;
}> = ({ user, onClose, onSubmit, isLoading, error }) => {
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  if (!user) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPassword) return;
    onSubmit(user.id, password);
  };

  const passwordsMatch = password === confirmPassword;
  const passwordValid = password.length >= 8;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <GlassCard className="w-full max-w-md">
        <h2 className="text-xl font-semibold mb-4">Reset Password for {user.display_name || user.username}</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              New Password *
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              placeholder="Minimum 8 characters"
              required
            />
            {password && !passwordValid && (
              <p className="text-xs text-red-400 mt-1">Password must be at least 8 characters</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Confirm Password *
            </label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              required
            />
            {confirmPassword && !passwordsMatch && (
              <p className="text-xs text-red-400 mt-1">Passwords do not match</p>
            )}
          </div>

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-label-secondary hover:text-label-primary transition-colors"
              disabled={isLoading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
              disabled={isLoading || !passwordValid || !passwordsMatch}
            >
              {isLoading ? <Spinner size="sm" /> : 'Reset Password'}
            </button>
          </div>
        </form>
      </GlassCard>
    </div>
  );
};

/**
 * Users Management Page
 */
export default function Users() {
  const [activeTab, setActiveTab] = useState<TabType>('users');
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [invites, setInvites] = useState<UserInvite[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modal states
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [resetPasswordUser, setResetPasswordUser] = useState<AdminUser | null>(null);
  const [modalLoading, setModalLoading] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);

  // Get current user ID from localStorage token (decoded)
  const currentUserId = React.useMemo(() => {
    try {
      const user = JSON.parse(localStorage.getItem('user') || '{}');
      return user.id || '';
    } catch {
      return '';
    }
  }, []);

  const loadData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [usersData, invitesData] = await Promise.all([
        usersApi.getUsers(),
        usersApi.getInvites(),
      ]);
      setUsers(usersData);
      setInvites(invitesData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleInviteUser = async (data: { email: string; role: string; display_name?: string }) => {
    setModalLoading(true);
    setModalError(null);
    try {
      await usersApi.inviteUser(data);
      setShowInviteModal(false);
      loadData();
    } catch (err) {
      setModalError(err instanceof Error ? err.message : 'Failed to send invite');
    } finally {
      setModalLoading(false);
    }
  };

  const handleEditUser = async (userId: string, data: { role?: string; display_name?: string }) => {
    setModalLoading(true);
    setModalError(null);
    try {
      await usersApi.updateUser(userId, data);
      setEditingUser(null);
      loadData();
    } catch (err) {
      setModalError(err instanceof Error ? err.message : 'Failed to update user');
    } finally {
      setModalLoading(false);
    }
  };

  const handleToggleStatus = async (user: AdminUser) => {
    if (!confirm(`Are you sure you want to ${user.status === 'active' ? 'disable' : 'enable'} ${user.display_name || user.username}?`)) {
      return;
    }
    try {
      await usersApi.updateUser(user.id, {
        status: user.status === 'active' ? 'disabled' : 'active',
      });
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update user status');
    }
  };

  const handleDeleteUser = async (user: AdminUser) => {
    if (!confirm(`Are you sure you want to delete ${user.display_name || user.username}? This cannot be undone.`)) {
      return;
    }
    try {
      await usersApi.deleteUser(user.id);
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete user');
    }
  };

  const handleResetPassword = async (userId: string, password: string) => {
    setModalLoading(true);
    setModalError(null);
    try {
      await usersApi.adminResetPassword(userId, password);
      setResetPasswordUser(null);
      // Show success message
      alert('Password reset successfully');
    } catch (err) {
      setModalError(err instanceof Error ? err.message : 'Failed to reset password');
    } finally {
      setModalLoading(false);
    }
  };

  const handleResendInvite = async (invite: UserInvite) => {
    try {
      await usersApi.resendInvite(invite.id);
      alert('Invite resent successfully');
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resend invite');
    }
  };

  const handleRevokeInvite = async (invite: UserInvite) => {
    if (!confirm(`Are you sure you want to revoke the invite for ${invite.email}?`)) {
      return;
    }
    try {
      await usersApi.revokeInvite(invite.id);
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke invite');
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-label-primary">User Management</h1>
          <p className="text-label-tertiary mt-1">
            Manage admin users and role-based access control
          </p>
        </div>
        <button
          onClick={() => setShowInviteModal(true)}
          className="px-4 py-2 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90 transition-colors flex items-center gap-2"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Invite User
        </button>
      </div>

      {error && (
        <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400">
          {error}
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        <GlassCard className="p-4">
          <p className="text-label-tertiary text-sm">Total Users</p>
          <p className="text-2xl font-bold text-label-primary">{users.length}</p>
        </GlassCard>
        <GlassCard className="p-4">
          <p className="text-label-tertiary text-sm">Active</p>
          <p className="text-2xl font-bold text-green-400">
            {users.filter(u => u.status === 'active').length}
          </p>
        </GlassCard>
        <GlassCard className="p-4">
          <p className="text-label-tertiary text-sm">Admins</p>
          <p className="text-2xl font-bold text-purple-400">
            {users.filter(u => u.role === 'admin').length}
          </p>
        </GlassCard>
        <GlassCard className="p-4">
          <p className="text-label-tertiary text-sm">Pending Invites</p>
          <p className="text-2xl font-bold text-yellow-400">{invites.length}</p>
        </GlassCard>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-fill-secondary p-1 rounded-lg w-fit">
        <button
          onClick={() => setActiveTab('users')}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            activeTab === 'users'
              ? 'bg-accent-primary text-white'
              : 'text-label-secondary hover:text-label-primary'
          }`}
        >
          Users ({users.length})
        </button>
        <button
          onClick={() => setActiveTab('invites')}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            activeTab === 'invites'
              ? 'bg-accent-primary text-white'
              : 'text-label-secondary hover:text-label-primary'
          }`}
        >
          Pending Invites ({invites.length})
        </button>
      </div>

      {/* Table */}
      <GlassCard>
        {activeTab === 'users' ? (
          <table className="w-full">
            <thead>
              <tr className="border-b border-separator-default">
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">User</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Email</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Role</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Status</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Last Login</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-separator-default">
              {users.map(user => (
                <UserRow
                  key={user.id}
                  user={user}
                  currentUserId={currentUserId}
                  onEdit={setEditingUser}
                  onToggleStatus={handleToggleStatus}
                  onDelete={handleDeleteUser}
                  onResetPassword={setResetPasswordUser}
                />
              ))}
            </tbody>
          </table>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-separator-default">
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Email</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Role</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Status</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Invited By</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Expires</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-separator-default">
              {invites.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-label-tertiary">
                    No pending invites
                  </td>
                </tr>
              ) : (
                invites.map(invite => (
                  <InviteRow
                    key={invite.id}
                    invite={invite}
                    onResend={handleResendInvite}
                    onRevoke={handleRevokeInvite}
                  />
                ))
              )}
            </tbody>
          </table>
        )}
      </GlassCard>

      {/* Modals */}
      <InviteUserModal
        isOpen={showInviteModal}
        onClose={() => {
          setShowInviteModal(false);
          setModalError(null);
        }}
        onSubmit={handleInviteUser}
        isLoading={modalLoading}
        error={modalError}
      />

      <EditUserModal
        user={editingUser}
        onClose={() => {
          setEditingUser(null);
          setModalError(null);
        }}
        onSubmit={handleEditUser}
        isLoading={modalLoading}
        error={modalError}
      />

      <ResetPasswordModal
        user={resetPasswordUser}
        onClose={() => {
          setResetPasswordUser(null);
          setModalError(null);
        }}
        onSubmit={handleResetPassword}
        isLoading={modalLoading}
        error={modalError}
      />
    </div>
  );
}

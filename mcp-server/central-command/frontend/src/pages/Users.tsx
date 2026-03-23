import React, { useState, useEffect, useCallback } from 'react';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { usersApi, AdminUser, UserInvite, Session, AuditLog, TotpSetup, UnifiedAccount } from '../utils/api';

type TabType = 'accounts' | 'users' | 'invites' | 'sessions' | 'audit' | 'security';

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
 * Parse user agent string into a readable browser/device string
 */
function parseUserAgent(ua: string): string {
  if (!ua) return 'Unknown';
  // Try to extract browser
  if (ua.includes('Firefox/')) return 'Firefox';
  if (ua.includes('Edg/')) return 'Edge';
  if (ua.includes('Chrome/') && !ua.includes('Edg/')) return 'Chrome';
  if (ua.includes('Safari/') && !ua.includes('Chrome/')) return 'Safari';
  if (ua.includes('curl/')) return 'curl';
  if (ua.includes('python')) return 'Python';
  // Fallback: return first 40 chars
  return ua.length > 40 ? ua.substring(0, 40) + '...' : ua;
}

/**
 * Role badge component
 */
const RoleBadge: React.FC<{ role: string }> = ({ role }) => {
  const variants: Record<string, 'default' | 'success' | 'warning' | 'error'> = {
    admin: 'error',
    operator: 'warning',
    readonly: 'default',
    companion: 'success',
  };
  return <Badge variant={variants[role] || 'default'}>{role}</Badge>;
};

/**
 * Status badge component
 */
const UserStatusBadge: React.FC<{ status: string }> = ({ status }) => {
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
 * Action badge for audit logs
 */
const ActionBadge: React.FC<{ action: string }> = ({ action }) => {
  const lower = action.toLowerCase();
  let variant: 'default' | 'success' | 'warning' | 'error' = 'default';
  if (lower.includes('login') || lower.includes('create')) variant = 'success';
  else if (lower.includes('delete') || lower.includes('revoke')) variant = 'error';
  else if (lower.includes('update') || lower.includes('change') || lower.includes('reset')) variant = 'warning';
  return <Badge variant={variant}>{action}</Badge>;
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
      <td className="px-4 py-3"><UserStatusBadge status={user.status} /></td>
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
                className={`text-xs ${user.status === 'active' ? 'text-health-warning' : 'text-health-healthy'} hover:underline`}
              >
                {user.status === 'active' ? 'Disable' : 'Enable'}
              </button>
              <button
                onClick={() => onDelete(user)}
                className="text-xs text-health-critical hover:underline"
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
        <UserStatusBadge status={isExpired ? 'expired' : invite.status} />
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
            className="text-xs text-health-critical hover:underline"
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
  const [role, setRole] = useState<'admin' | 'operator' | 'readonly' | 'companion'>('operator');
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
              onChange={(e) => setRole(e.target.value as 'admin' | 'operator' | 'readonly' | 'companion')}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
            >
              <option value="admin">Admin - Full access including user management</option>
              <option value="operator">Operator - View and execute actions</option>
              <option value="readonly">Readonly - View only access</option>
              <option value="companion">Companion - HIPAA compliance guidance across clients</option>
            </select>
          </div>

          {error && (
            <div className="p-3 bg-health-critical/10 border border-health-critical/20 rounded-lg text-health-critical text-sm">
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
  onSubmit: (userId: string, data: { role?: string; display_name?: string; email?: string }) => void;
  isLoading: boolean;
  error: string | null;
}> = ({ user, onClose, onSubmit, isLoading, error }) => {
  const [role, setRole] = useState<string>(user?.role || 'operator');
  const [displayName, setDisplayName] = useState(user?.display_name || '');
  const [email, setEmail] = useState(user?.email || '');

  useEffect(() => {
    if (user) {
      setRole(user.role);
      setDisplayName(user.display_name || '');
      setEmail(user.email || '');
    }
  }, [user]);

  if (!user) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(user.id, {
      role: role !== user.role ? role : undefined,
      display_name: displayName !== (user.display_name || '') ? displayName : undefined,
      email: email !== (user.email || '') ? email : undefined,
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
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              placeholder="user@example.com"
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
            <div className="p-3 bg-health-critical/10 border border-health-critical/20 rounded-lg text-health-critical text-sm">
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
  const passwordValid = password.length >= 12;

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
              placeholder="Minimum 12 characters"
              required
            />
            {password && !passwordValid && (
              <p className="text-xs text-health-critical mt-1">Password must be at least 12 characters</p>
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
              <p className="text-xs text-health-critical mt-1">Passwords do not match</p>
            )}
          </div>

          {error && (
            <div className="p-3 bg-health-critical/10 border border-health-critical/20 rounded-lg text-health-critical text-sm">
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
 * Change My Password Modal
 */
const ChangePasswordModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  isLoading: boolean;
  error: string | null;
  onSubmit: (data: { current_password: string; new_password: string; confirm_password: string }) => void;
}> = ({ isOpen, onClose, isLoading, error, onSubmit }) => {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  if (!isOpen) return null;

  const passwordValid = newPassword.length >= 12;
  const passwordsMatch = newPassword === confirmPassword;
  const hasUpper = /[A-Z]/.test(newPassword);
  const hasLower = /[a-z]/.test(newPassword);
  const hasNumber = /[0-9]/.test(newPassword);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!passwordValid || !passwordsMatch) return;
    onSubmit({
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <GlassCard className="w-full max-w-md">
        <h2 className="text-xl font-semibold mb-4">Change My Password</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Current Password *
            </label>
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              New Password *
            </label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              placeholder="Minimum 12 characters"
              required
            />
            {newPassword && (
              <div className="mt-2 space-y-1">
                <p className={`text-xs ${newPassword.length >= 12 ? 'text-health-healthy' : 'text-health-critical'}`}>
                  {newPassword.length >= 12 ? '\u2713' : '\u2717'} At least 12 characters ({newPassword.length}/12)
                </p>
                <p className={`text-xs ${hasUpper ? 'text-health-healthy' : 'text-label-tertiary'}`}>
                  {hasUpper ? '\u2713' : '\u2717'} Uppercase letter
                </p>
                <p className={`text-xs ${hasLower ? 'text-health-healthy' : 'text-label-tertiary'}`}>
                  {hasLower ? '\u2713' : '\u2717'} Lowercase letter
                </p>
                <p className={`text-xs ${hasNumber ? 'text-health-healthy' : 'text-label-tertiary'}`}>
                  {hasNumber ? '\u2713' : '\u2717'} Number
                </p>
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Confirm New Password *
            </label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              required
            />
            {confirmPassword && !passwordsMatch && (
              <p className="text-xs text-health-critical mt-1">Passwords do not match</p>
            )}
          </div>

          {error && (
            <div className="p-3 bg-health-critical/10 border border-health-critical/20 rounded-lg text-health-critical text-sm">
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
              disabled={isLoading || !passwordValid || !passwordsMatch || !currentPassword}
            >
              {isLoading ? <Spinner size="sm" /> : 'Change Password'}
            </button>
          </div>
        </form>
      </GlassCard>
    </div>
  );
};

/**
 * Sessions Tab Content
 */
const SessionsTab: React.FC = () => {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await usersApi.getSessions();
      setSessions(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sessions');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const handleRevoke = async (sessionId: string) => {
    if (!confirm('Revoke this session? The user will be logged out.')) return;
    try {
      await usersApi.revokeSession(sessionId);
      loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke session');
    }
  };

  const handleRevokeAll = async () => {
    if (!confirm('Revoke all other sessions? You will remain logged in.')) return;
    try {
      await usersApi.revokeAllSessions();
      loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke sessions');
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-32">
        <Spinner />
      </div>
    );
  }

  return (
    <GlassCard>
      <div className="flex items-center justify-between px-4 py-3 border-b border-separator-default">
        <h3 className="text-sm font-medium text-label-primary">Active Sessions</h3>
        {sessions.length > 1 && (
          <button
            onClick={handleRevokeAll}
            className="text-xs text-health-critical hover:underline"
          >
            Revoke All Other Sessions
          </button>
        )}
      </div>

      {error && (
        <div className="mx-4 mt-3 p-3 bg-health-critical/10 border border-health-critical/20 rounded-lg text-health-critical text-sm">
          {error}
        </div>
      )}

      <table className="w-full">
        <thead>
          <tr className="border-b border-separator-default">
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">IP Address</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Browser / Device</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Last Active</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Created</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-separator-default">
          {sessions.length === 0 ? (
            <tr>
              <td colSpan={5} className="px-4 py-8 text-center text-label-tertiary">
                No active sessions
              </td>
            </tr>
          ) : (
            sessions.map(session => (
              <tr key={session.id} className="hover:bg-fill-tertiary/50 transition-colors">
                <td className="px-4 py-3 text-sm text-label-primary font-mono">
                  {session.ip_address || '-'}
                </td>
                <td className="px-4 py-3 text-sm text-label-secondary">
                  {parseUserAgent(session.user_agent)}
                </td>
                <td className="px-4 py-3 text-sm text-label-tertiary">
                  {formatDate(session.last_activity_at)}
                </td>
                <td className="px-4 py-3 text-sm text-label-tertiary">
                  {formatDate(session.created_at)}
                </td>
                <td className="px-4 py-3">
                  {session.is_current ? (
                    <Badge variant="success">current</Badge>
                  ) : (
                    <button
                      onClick={() => handleRevoke(session.id)}
                      className="text-xs text-health-critical hover:underline"
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </GlassCard>
  );
};

/**
 * Audit Log Tab Content
 */
const AuditLogTab: React.FC = () => {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadLogs = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await usersApi.getAuditLogs(100);
        setLogs(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load audit logs');
      } finally {
        setIsLoading(false);
      }
    };
    loadLogs();
  }, []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-32">
        <Spinner />
      </div>
    );
  }

  return (
    <GlassCard>
      {error && (
        <div className="mx-4 mt-3 p-3 bg-health-critical/10 border border-health-critical/20 rounded-lg text-health-critical text-sm">
          {error}
        </div>
      )}

      <table className="w-full">
        <thead>
          <tr className="border-b border-separator-default">
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">User</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Action</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Target</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Details</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">IP</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-label-tertiary">Time</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-separator-default">
          {logs.length === 0 ? (
            <tr>
              <td colSpan={6} className="px-4 py-8 text-center text-label-tertiary">
                No audit log entries
              </td>
            </tr>
          ) : (
            logs.map(log => (
              <tr key={log.id} className="hover:bg-fill-tertiary/50 transition-colors">
                <td className="px-4 py-3 text-sm text-label-primary">{log.user}</td>
                <td className="px-4 py-3"><ActionBadge action={log.action} /></td>
                <td className="px-4 py-3 text-sm text-label-secondary">{log.target || '-'}</td>
                <td className="px-4 py-3 text-sm text-label-tertiary max-w-xs truncate">
                  {log.details && Object.keys(log.details).length > 0
                    ? JSON.stringify(log.details)
                    : '-'}
                </td>
                <td className="px-4 py-3 text-sm text-label-tertiary font-mono">{log.ip || '-'}</td>
                <td className="px-4 py-3 text-sm text-label-tertiary">{formatDate(log.timestamp)}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </GlassCard>
  );
};

/**
 * Security / 2FA Tab Content
 */
const SecurityTab: React.FC = () => {
  const [totpEnabled, setTotpEnabled] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [setupData, setSetupData] = useState<TotpSetup | null>(null);
  const [verifyCode, setVerifyCode] = useState('');
  const [verifyPassword, setVerifyPassword] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [disablePassword, setDisablePassword] = useState('');
  const [showDisable, setShowDisable] = useState(false);
  const [disabling, setDisabling] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    const loadProfile = async () => {
      setIsLoading(true);
      try {
        const profile = await usersApi.getProfile();
        // Check if totp_enabled is in the profile (backend may include it)
        setTotpEnabled((profile as AdminUser & { totp_enabled?: boolean }).totp_enabled || false);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load profile');
      } finally {
        setIsLoading(false);
      }
    };
    loadProfile();
  }, []);

  const handleSetup = async () => {
    setError(null);
    setSuccessMsg(null);
    try {
      const data = await usersApi.setupTotp();
      setSetupData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start 2FA setup');
    }
  };

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    setVerifying(true);
    setError(null);
    try {
      await usersApi.verifyTotp(verifyCode, verifyPassword);
      setTotpEnabled(true);
      setSetupData(null);
      setVerifyCode('');
      setVerifyPassword('');
      setSuccessMsg('Two-factor authentication has been enabled.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to verify code');
    } finally {
      setVerifying(false);
    }
  };

  const handleDisable = async (e: React.FormEvent) => {
    e.preventDefault();
    setDisabling(true);
    setError(null);
    try {
      await usersApi.disableTotp(disablePassword);
      setTotpEnabled(false);
      setShowDisable(false);
      setDisablePassword('');
      setSuccessMsg('Two-factor authentication has been disabled.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to disable 2FA');
    } finally {
      setDisabling(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-32">
        <Spinner />
      </div>
    );
  }

  return (
    <GlassCard className="p-6">
      <h3 className="text-lg font-semibold text-label-primary mb-4">Two-Factor Authentication</h3>

      {error && (
        <div className="mb-4 p-3 bg-health-critical/10 border border-health-critical/20 rounded-lg text-health-critical text-sm">
          {error}
        </div>
      )}

      {successMsg && (
        <div className="mb-4 p-3 bg-health-healthy/10 border border-health-healthy/20 rounded-lg text-health-healthy text-sm">
          {successMsg}
        </div>
      )}

      <div className="flex items-center gap-3 mb-6">
        <span className="text-sm text-label-secondary">Status:</span>
        {totpEnabled ? (
          <Badge variant="success">Enabled</Badge>
        ) : (
          <Badge variant="default">Disabled</Badge>
        )}
      </div>

      {/* 2FA not enabled - show setup flow */}
      {!totpEnabled && !setupData && (
        <button
          onClick={handleSetup}
          className="px-4 py-2 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90 transition-colors"
        >
          Enable 2FA
        </button>
      )}

      {/* Setup in progress - show QR + backup codes + verify */}
      {!totpEnabled && setupData && (
        <div className="space-y-6">
          <div>
            <h4 className="text-sm font-medium text-label-primary mb-2">
              1. Scan QR Code with your authenticator app
            </h4>
            <div className="flex justify-center p-4 bg-white rounded-lg w-fit">
              <img
                src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(setupData.qr_uri)}`}
                alt="TOTP QR Code"
                width={200}
                height={200}
              />
            </div>
            <p className="text-xs text-label-tertiary mt-2">
              Or enter this secret manually: <code className="bg-fill-secondary px-2 py-1 rounded text-label-secondary">{setupData.secret}</code>
            </p>
          </div>

          <div>
            <h4 className="text-sm font-medium text-label-primary mb-2">
              2. Save your backup codes
            </h4>
            <div className="p-3 bg-fill-secondary border border-separator-default rounded-lg">
              <p className="text-xs text-health-warning mb-2">
                Save these codes in a safe place. Each code can only be used once.
              </p>
              <div className="grid grid-cols-2 gap-2">
                {setupData.backup_codes.map((code, i) => (
                  <code key={i} className="text-sm text-label-primary bg-fill-tertiary px-2 py-1 rounded text-center">
                    {code}
                  </code>
                ))}
              </div>
            </div>
          </div>

          <div>
            <h4 className="text-sm font-medium text-label-primary mb-2">
              3. Enter verification code from your authenticator app
            </h4>
            <form onSubmit={handleVerify} className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-label-secondary mb-1">
                  6-digit code
                </label>
                <input
                  type="text"
                  value={verifyCode}
                  onChange={(e) => setVerifyCode(e.target.value)}
                  className="w-full max-w-xs px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary font-mono text-lg tracking-widest"
                  placeholder="000000"
                  maxLength={6}
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-label-secondary mb-1">
                  Current password (to confirm)
                </label>
                <input
                  type="password"
                  value={verifyPassword}
                  onChange={(e) => setVerifyPassword(e.target.value)}
                  className="w-full max-w-xs px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
                  required
                />
              </div>
              <div className="flex gap-3">
                <button
                  type="submit"
                  className="px-4 py-2 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
                  disabled={verifying || verifyCode.length < 6 || !verifyPassword}
                >
                  {verifying ? <Spinner size="sm" /> : 'Verify & Enable'}
                </button>
                <button
                  type="button"
                  onClick={() => setSetupData(null)}
                  className="px-4 py-2 text-label-secondary hover:text-label-primary transition-colors"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 2FA enabled - show disable option */}
      {totpEnabled && !showDisable && (
        <button
          onClick={() => {
            setShowDisable(true);
            setSuccessMsg(null);
          }}
          className="px-4 py-2 bg-health-critical/20 text-health-critical border border-health-critical/30 rounded-lg hover:bg-health-critical/30 transition-colors"
        >
          Disable 2FA
        </button>
      )}

      {totpEnabled && showDisable && (
        <form onSubmit={handleDisable} className="space-y-3 max-w-sm">
          <p className="text-sm text-label-secondary">
            Enter your password to confirm disabling two-factor authentication.
          </p>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Password
            </label>
            <input
              type="password"
              value={disablePassword}
              onChange={(e) => setDisablePassword(e.target.value)}
              className="w-full px-3 py-2 bg-fill-secondary border border-separator-default rounded-lg text-label-primary focus:outline-none focus:border-accent-primary"
              required
            />
          </div>
          <div className="flex gap-3">
            <button
              type="submit"
              className="px-4 py-2 bg-health-critical text-white rounded-lg hover:bg-health-critical/90 transition-colors disabled:opacity-50"
              disabled={disabling || !disablePassword}
            >
              {disabling ? <Spinner size="sm" /> : 'Confirm Disable'}
            </button>
            <button
              type="button"
              onClick={() => {
                setShowDisable(false);
                setDisablePassword('');
              }}
              className="px-4 py-2 text-label-secondary hover:text-label-primary transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </GlassCard>
  );
};

// ============================================================================
// All Accounts Tab (unified admin + partner + client users)
// ============================================================================
const AccountTypeBadge: React.FC<{ type: string }> = ({ type }) => {
  const variants: Record<string, 'default' | 'success' | 'warning' | 'error'> = {
    admin: 'error',
    partner: 'warning',
    client: 'success',
  };
  return <Badge variant={variants[type] || 'default'}>{type}</Badge>;
};

const AllAccountsTab: React.FC = () => {
  const [accounts, setAccounts] = useState<UnifiedAccount[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const [sortBy, setSortBy] = useState('name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<Record<string, number>>({});
  const limit = 25;

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const fetchAccounts = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await usersApi.getAllAccounts({
        search: debouncedSearch || undefined,
        account_type: typeFilter,
        sort_by: sortBy,
        sort_dir: sortDir,
        limit,
        offset: page * limit,
      });
      setAccounts(data.accounts);
      setTotal(data.total);
      if (data.stats) setStats(data.stats);
    } catch (err) {
      console.error('Failed to fetch accounts:', err);
    } finally {
      setIsLoading(false);
    }
  }, [debouncedSearch, typeFilter, sortBy, sortDir, page]);

  useEffect(() => { fetchAccounts(); }, [fetchAccounts]);

  const totalPages = Math.ceil(total / limit);

  const handleSort = (col: string) => {
    if (sortBy === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(col); setSortDir('asc'); }
    setPage(0);
  };

  const SortIcon: React.FC<{ col: string }> = ({ col }) => {
    if (sortBy !== col) return <span className="text-label-quaternary ml-1">↕</span>;
    return <span className="text-accent-primary ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  };

  const thClass = "px-4 py-3 text-left text-sm font-medium text-label-tertiary cursor-pointer select-none hover:text-label-primary";

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <GlassCard className="p-3 text-center">
          <p className="text-xl font-bold text-label-primary">{stats.total || 0}</p>
          <p className="text-xs text-label-tertiary">Total</p>
        </GlassCard>
        <GlassCard className="p-3 text-center">
          <p className="text-xl font-bold text-health-critical">{stats.admin || 0}</p>
          <p className="text-xs text-label-tertiary">Admin</p>
        </GlassCard>
        <GlassCard className="p-3 text-center">
          <p className="text-xl font-bold text-health-warning">{stats.partner || 0}</p>
          <p className="text-xs text-label-tertiary">Partner</p>
        </GlassCard>
        <GlassCard className="p-3 text-center">
          <p className="text-xl font-bold text-health-healthy">{stats.client || 0}</p>
          <p className="text-xs text-label-tertiary">Client</p>
        </GlassCard>
        <GlassCard className="p-3 text-center">
          <p className="text-xl font-bold text-health-healthy">{stats.active || 0}</p>
          <p className="text-xs text-label-tertiary">Active</p>
        </GlassCard>
        <GlassCard className="p-3 text-center">
          <p className="text-xl font-bold text-accent-primary">{stats.mfa_enabled || 0}</p>
          <p className="text-xs text-label-tertiary">MFA On</p>
        </GlassCard>
      </div>

      {/* Search + Filter */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search all accounts..."
            className="w-full pl-10 pr-3 py-2 text-sm border border-separator-light rounded-ios bg-fill-primary focus:ring-2 focus:ring-accent-primary focus:border-transparent"
          />
        </div>
        <div className="flex gap-1">
          {[
            { value: undefined, label: 'All' },
            { value: 'admin', label: 'Admin' },
            { value: 'partner', label: 'Partner' },
            { value: 'client', label: 'Client' },
          ].map(option => (
            <button key={option.value || 'all'} onClick={() => { setTypeFilter(option.value); setPage(0); }}
              className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                typeFilter === option.value ? 'bg-accent-primary text-white' : 'bg-separator-light text-label-secondary hover:bg-separator-light/80'
              }`}>
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <GlassCard padding="none">
        {isLoading ? (
          <div className="flex items-center justify-center py-12"><Spinner size="lg" /></div>
        ) : accounts.length === 0 ? (
          <div className="text-center py-12 text-label-tertiary">
            {debouncedSearch ? 'No accounts match your search.' : 'No accounts found.'}
          </div>
        ) : (
          <>
            <table className="w-full">
              <thead className="border-b border-separator-default bg-fill-secondary">
                <tr>
                  <th className={thClass} onClick={() => handleSort('name')}>Name<SortIcon col="name" /></th>
                  <th className={thClass} onClick={() => handleSort('email')}>Email<SortIcon col="email" /></th>
                  <th className={thClass} onClick={() => handleSort('type')}>Type<SortIcon col="type" /></th>
                  <th className={thClass} onClick={() => handleSort('org')}>Organization<SortIcon col="org" /></th>
                  <th className={thClass}>Role</th>
                  <th className={thClass} onClick={() => handleSort('status')}>Status<SortIcon col="status" /></th>
                  <th className={thClass}>MFA</th>
                  <th className={thClass} onClick={() => handleSort('last_login')}>Last Login<SortIcon col="last_login" /></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator-default">
                {accounts.map(acct => (
                  <tr key={`${acct.account_type}-${acct.id}`} className="hover:bg-fill-tertiary/50 transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold text-sm ${
                          acct.account_type === 'admin' ? 'bg-health-critical' : acct.account_type === 'partner' ? 'bg-health-warning' : 'bg-health-healthy'
                        }`}>
                          {(acct.name || '?').charAt(0).toUpperCase()}
                        </div>
                        <span className="font-medium text-label-primary">{acct.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-label-secondary">{acct.email}</td>
                    <td className="px-4 py-3"><AccountTypeBadge type={acct.account_type} /></td>
                    <td className="px-4 py-3 text-sm text-label-secondary">{acct.org}</td>
                    <td className="px-4 py-3"><RoleBadge role={acct.role} /></td>
                    <td className="px-4 py-3"><UserStatusBadge status={acct.status} /></td>
                    <td className="px-4 py-3">
                      {acct.mfa_enabled ? (
                        <span className="text-health-healthy text-sm">Enabled</span>
                      ) : (
                        <span className="text-label-quaternary text-sm">Off</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-label-tertiary">{acct.last_login ? formatDate(acct.last_login) : 'Never'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {totalPages > 1 && (
              <div className="flex items-center justify-between px-4 py-3 border-t border-separator-light bg-fill-secondary">
                <p className="text-sm text-label-tertiary">
                  Showing {page * limit + 1}–{Math.min((page + 1) * limit, total)} of {total}
                </p>
                <div className="flex items-center gap-1">
                  <button onClick={() => setPage(0)} disabled={page === 0}
                    className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">««</button>
                  <button onClick={() => setPage(p => p - 1)} disabled={page === 0}
                    className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">«</button>
                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    let p: number;
                    if (totalPages <= 5) p = i;
                    else if (page < 3) p = i;
                    else if (page > totalPages - 4) p = totalPages - 5 + i;
                    else p = page - 2 + i;
                    return (
                      <button key={p} onClick={() => setPage(p)}
                        className={`px-3 py-1 text-sm rounded ${p === page ? 'bg-accent-primary text-white' : 'hover:bg-fill-tertiary text-label-secondary'}`}>
                        {p + 1}
                      </button>
                    );
                  })}
                  <button onClick={() => setPage(p => p + 1)} disabled={page >= totalPages - 1}
                    className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">»</button>
                  <button onClick={() => setPage(totalPages - 1)} disabled={page >= totalPages - 1}
                    className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">»»</button>
                </div>
              </div>
            )}
          </>
        )}
      </GlassCard>
    </div>
  );
};

/**
 * Users Management Page
 */
export default function Users() {
  const [activeTab, setActiveTab] = useState<TabType>('accounts');
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [invites, setInvites] = useState<UserInvite[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modal states
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [showChangePasswordModal, setShowChangePasswordModal] = useState(false);
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

  const handleEditUser = async (userId: string, data: { role?: string; display_name?: string; email?: string }) => {
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

  const handleChangePassword = async (data: { current_password: string; new_password: string; confirm_password: string }) => {
    setModalLoading(true);
    setModalError(null);
    try {
      await usersApi.changePassword(data);
      setShowChangePasswordModal(false);
      alert('Password changed successfully');
    } catch (err) {
      setModalError(err instanceof Error ? err.message : 'Failed to change password');
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

  const tabs: { key: TabType; label: string }[] = [
    { key: 'accounts', label: 'All Accounts' },
    { key: 'users', label: `Admin Users (${users.length})` },
    { key: 'invites', label: `Invites (${invites.length})` },
    { key: 'sessions', label: 'Sessions' },
    { key: 'audit', label: 'Audit Log' },
    { key: 'security', label: 'Security / 2FA' },
  ];

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
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setShowChangePasswordModal(true);
              setModalError(null);
            }}
            className="px-4 py-2 bg-fill-secondary text-label-primary border border-separator-default rounded-lg hover:bg-fill-tertiary transition-colors flex items-center gap-2"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
            </svg>
            Change My Password
          </button>
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
      </div>

      {error && (
        <div className="p-4 bg-health-critical/10 border border-health-critical/20 rounded-lg text-health-critical">
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
          <p className="text-2xl font-bold text-health-healthy">
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
          <p className="text-2xl font-bold text-health-warning">{invites.length}</p>
        </GlassCard>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-fill-secondary p-1 rounded-lg w-fit">
        {tabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'bg-accent-primary text-white'
                : 'text-label-secondary hover:text-label-primary'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'accounts' && <AllAccountsTab />}

      {activeTab === 'users' && (
        <GlassCard>
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
        </GlassCard>
      )}

      {activeTab === 'invites' && (
        <GlassCard>
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
        </GlassCard>
      )}

      {activeTab === 'sessions' && <SessionsTab />}
      {activeTab === 'audit' && <AuditLogTab />}
      {activeTab === 'security' && <SecurityTab />}

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

      <ChangePasswordModal
        isOpen={showChangePasswordModal}
        onClose={() => {
          setShowChangePasswordModal(false);
          setModalError(null);
        }}
        onSubmit={handleChangePassword}
        isLoading={modalLoading}
        error={modalError}
      />
    </div>
  );
}

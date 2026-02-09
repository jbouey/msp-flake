import React, { useState, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';

interface ProviderConfig {
  client_id: string | null;
  tenant_id: string | null;
  enabled: boolean;
  allow_registration: boolean;
  default_role: 'admin' | 'operator' | 'readonly';
  require_admin_approval: boolean;
  allowed_domains: string[];
  created_at: string | null;
  updated_at: string | null;
}

interface OAuthConfig {
  providers: {
    google?: ProviderConfig;
    microsoft?: ProviderConfig;
  };
}

interface PendingUser {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: string;
  created_at: string | null;
  oauth_provider: string | null;
  oauth_email: string | null;
}

const getToken = (): string | null => localStorage.getItem('auth_token');

export const AdminOAuthSettings: React.FC = () => {
  const { user } = useAuth();
  const [config, setConfig] = useState<OAuthConfig | null>(null);
  const [pendingUsers, setPendingUsers] = useState<PendingUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Form state for editing providers
  const [editingProvider, setEditingProvider] = useState<'google' | 'microsoft' | null>(null);
  const [formData, setFormData] = useState({
    client_id: '',
    client_secret: '',
    tenant_id: '',
    enabled: false,
    allow_registration: true,
    default_role: 'readonly' as 'admin' | 'operator' | 'readonly',
    require_admin_approval: true,
    allowed_domains: '',
  });

  // Fetch OAuth config and pending users
  const fetchData = async () => {
    const token = getToken();
    if (!token) return;

    try {
      const [configRes, pendingRes] = await Promise.all([
        fetch('/api/admin/oauth/config', { headers: { Authorization: `Bearer ${token}` } }),
        fetch('/api/admin/oauth/pending', { headers: { Authorization: `Bearer ${token}` } }),
      ]);

      if (configRes.ok) {
        const data = await configRes.json();
        setConfig(data);
      }

      if (pendingRes.ok) {
        const data = await pendingRes.json();
        setPendingUsers(data.pending_users || []);
      }
    } catch (err) {
      setError('Failed to load OAuth settings');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleEditProvider = (provider: 'google' | 'microsoft') => {
    const providerConfig = config?.providers[provider];
    setFormData({
      client_id: providerConfig?.client_id || '',
      client_secret: '', // Don't pre-fill secret
      tenant_id: providerConfig?.tenant_id || '',
      enabled: providerConfig?.enabled || false,
      allow_registration: providerConfig?.allow_registration ?? true,
      default_role: providerConfig?.default_role || 'readonly',
      require_admin_approval: providerConfig?.require_admin_approval ?? true,
      allowed_domains: providerConfig?.allowed_domains?.join(', ') || '',
    });
    setEditingProvider(provider);
  };

  const handleSaveProvider = async () => {
    if (!editingProvider) return;

    const token = getToken();
    if (!token) return;

    setSaving(editingProvider);
    setError(null);

    try {
      const payload: Record<string, unknown> = {
        client_id: formData.client_id,
        enabled: formData.enabled,
        allow_registration: formData.allow_registration,
        default_role: formData.default_role,
        require_admin_approval: formData.require_admin_approval,
        allowed_domains: formData.allowed_domains
          .split(',')
          .map(d => d.trim().toLowerCase())
          .filter(d => d),
      };

      // Only include client_secret if provided
      if (formData.client_secret) {
        payload.client_secret = formData.client_secret;
      }

      // Include tenant_id for Microsoft
      if (editingProvider === 'microsoft') {
        payload.tenant_id = formData.tenant_id || 'common';
      }

      const response = await fetch(`/api/admin/oauth/config/${editingProvider}`, {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      if (response.ok) {
        setSuccess(`${editingProvider === 'google' ? 'Google' : 'Microsoft'} OAuth settings saved`);
        setEditingProvider(null);
        fetchData();
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to save settings');
      }
    } catch (err) {
      setError('Failed to save settings');
    } finally {
      setSaving(null);
    }
  };

  const handleApproveUser = async (userId: string) => {
    const token = getToken();
    if (!token) return;

    try {
      const response = await fetch(`/api/admin/oauth/approve/${userId}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });

      if (response.ok) {
        setSuccess('User approved');
        fetchData();
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to approve user');
      }
    } catch (err) {
      setError('Failed to approve user');
    }
  };

  const handleRejectUser = async (userId: string) => {
    if (!confirm('Are you sure you want to reject and delete this user?')) return;

    const token = getToken();
    if (!token) return;

    try {
      const response = await fetch(`/api/admin/oauth/reject/${userId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });

      if (response.ok) {
        setSuccess('User rejected');
        fetchData();
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to reject user');
      }
    } catch (err) {
      setError('Failed to reject user');
    }
  };

  // Only admins can access this page
  if (user?.role !== 'admin') {
    return (
      <div className="p-8">
        <h1 className="text-2xl font-bold text-red-600">Access Denied</h1>
        <p className="text-gray-600 mt-2">You need admin privileges to access OAuth settings.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="p-8 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-accent-primary"></div>
      </div>
    );
  }

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-label-primary mb-6">OAuth Login Settings</h1>

      {/* Alerts */}
      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-ios-md">
          <p className="text-red-700">{error}</p>
          <button onClick={() => setError(null)} className="text-red-600 underline text-sm mt-1">
            Dismiss
          </button>
        </div>
      )}

      {success && (
        <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded-ios-md">
          <p className="text-green-700">{success}</p>
          <button onClick={() => setSuccess(null)} className="text-green-600 underline text-sm mt-1">
            Dismiss
          </button>
        </div>
      )}

      {/* Provider Cards */}
      <div className="space-y-6">
        {/* Google */}
        <div className="bg-white rounded-ios-lg shadow border border-gray-200 p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <svg className="w-8 h-8" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
              </svg>
              <div>
                <h2 className="text-lg font-semibold text-label-primary">Google</h2>
                <p className="text-sm text-label-secondary">Sign in with Google accounts</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className={`px-3 py-1 rounded-full text-sm font-medium ${config?.providers.google?.enabled ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                {config?.providers.google?.enabled ? 'Enabled' : 'Disabled'}
              </span>
              <button
                onClick={() => handleEditProvider('google')}
                className="px-4 py-2 bg-accent-primary text-white rounded-ios-md hover:bg-accent-primary/90"
              >
                Configure
              </button>
            </div>
          </div>
          {config?.providers.google?.client_id && config.providers.google.client_id !== 'not-configured' && (
            <p className="text-sm text-label-tertiary">Client ID: {config.providers.google.client_id.slice(0, 20)}...</p>
          )}
        </div>

        {/* Microsoft */}
        <div className="bg-white rounded-ios-lg shadow border border-gray-200 p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <svg className="w-8 h-8" viewBox="0 0 23 23">
                <rect fill="#f35325" x="1" y="1" width="10" height="10"/>
                <rect fill="#81bc06" x="12" y="1" width="10" height="10"/>
                <rect fill="#05a6f0" x="1" y="12" width="10" height="10"/>
                <rect fill="#ffba08" x="12" y="12" width="10" height="10"/>
              </svg>
              <div>
                <h2 className="text-lg font-semibold text-label-primary">Microsoft</h2>
                <p className="text-sm text-label-secondary">Sign in with Microsoft / Azure AD accounts</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className={`px-3 py-1 rounded-full text-sm font-medium ${config?.providers.microsoft?.enabled ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                {config?.providers.microsoft?.enabled ? 'Enabled' : 'Disabled'}
              </span>
              <button
                onClick={() => handleEditProvider('microsoft')}
                className="px-4 py-2 bg-accent-primary text-white rounded-ios-md hover:bg-accent-primary/90"
              >
                Configure
              </button>
            </div>
          </div>
          {config?.providers.microsoft?.client_id && config.providers.microsoft.client_id !== 'not-configured' && (
            <p className="text-sm text-label-tertiary">Client ID: {config.providers.microsoft.client_id.slice(0, 20)}...</p>
          )}
        </div>
      </div>

      {/* Pending Users */}
      {pendingUsers.length > 0 && (
        <div className="mt-8">
          <h2 className="text-xl font-semibold text-label-primary mb-4">Pending Approvals</h2>
          <div className="bg-white rounded-ios-lg shadow border border-gray-200 overflow-hidden">
            <table className="w-full">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">User</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Provider</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Requested</th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-label-secondary">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {pendingUsers.map((pendingUser) => (
                  <tr key={pendingUser.id}>
                    <td className="px-4 py-3">
                      <div>
                        <p className="font-medium text-label-primary">{pendingUser.display_name}</p>
                        <p className="text-sm text-label-secondary">{pendingUser.email}</p>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className="capitalize">{pendingUser.oauth_provider || 'Unknown'}</span>
                    </td>
                    <td className="px-4 py-3 text-sm text-label-secondary">
                      {pendingUser.created_at ? new Date(pendingUser.created_at).toLocaleDateString() : '-'}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => handleApproveUser(pendingUser.id)}
                        className="px-3 py-1 bg-green-600 text-white rounded-md hover:bg-green-700 mr-2"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => handleRejectUser(pendingUser.id)}
                        className="px-3 py-1 bg-red-600 text-white rounded-md hover:bg-red-700"
                      >
                        Reject
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editingProvider && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-ios-lg shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto p-6">
            <h2 className="text-xl font-semibold text-label-primary mb-4">
              Configure {editingProvider === 'google' ? 'Google' : 'Microsoft'} OAuth
            </h2>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-label-primary mb-1">Client ID</label>
                <input
                  type="text"
                  value={formData.client_id}
                  onChange={(e) => setFormData({ ...formData, client_id: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-ios-md focus:ring-2 focus:ring-accent-primary focus:border-transparent"
                  placeholder="Enter OAuth Client ID"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-label-primary mb-1">Client Secret</label>
                <input
                  type="password"
                  value={formData.client_secret}
                  onChange={(e) => setFormData({ ...formData, client_secret: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-ios-md focus:ring-2 focus:ring-accent-primary focus:border-transparent"
                  placeholder="Enter new secret (leave blank to keep existing)"
                />
              </div>

              {editingProvider === 'microsoft' && (
                <div>
                  <label className="block text-sm font-medium text-label-primary mb-1">Tenant ID</label>
                  <input
                    type="text"
                    value={formData.tenant_id}
                    onChange={(e) => setFormData({ ...formData, tenant_id: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-ios-md focus:ring-2 focus:ring-accent-primary focus:border-transparent"
                    placeholder="common (or specific tenant ID)"
                  />
                  <p className="text-xs text-label-tertiary mt-1">Use "common" for any Microsoft account, or your Azure AD tenant ID</p>
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-label-primary mb-1">Allowed Email Domains</label>
                <input
                  type="text"
                  value={formData.allowed_domains}
                  onChange={(e) => setFormData({ ...formData, allowed_domains: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-ios-md focus:ring-2 focus:ring-accent-primary focus:border-transparent"
                  placeholder="company.com, contractor.company.com"
                />
                <p className="text-xs text-label-tertiary mt-1">Comma-separated list. Leave empty to allow all domains.</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-label-primary mb-1">Default Role for New Users</label>
                <select
                  value={formData.default_role}
                  onChange={(e) => setFormData({ ...formData, default_role: e.target.value as 'admin' | 'operator' | 'readonly' })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-ios-md focus:ring-2 focus:ring-accent-primary focus:border-transparent"
                >
                  <option value="readonly">Read-only</option>
                  <option value="operator">Operator</option>
                  <option value="admin">Admin</option>
                </select>
              </div>

              <div className="space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.enabled}
                    onChange={(e) => setFormData({ ...formData, enabled: e.target.checked })}
                    className="rounded"
                  />
                  <span className="text-sm text-label-primary">Enable this provider</span>
                </label>

                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.allow_registration}
                    onChange={(e) => setFormData({ ...formData, allow_registration: e.target.checked })}
                    className="rounded"
                  />
                  <span className="text-sm text-label-primary">Allow new user registration</span>
                </label>

                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.require_admin_approval}
                    onChange={(e) => setFormData({ ...formData, require_admin_approval: e.target.checked })}
                    className="rounded"
                  />
                  <span className="text-sm text-label-primary">Require admin approval for new users</span>
                </label>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6 pt-4 border-t">
              <button
                onClick={() => setEditingProvider(null)}
                className="px-4 py-2 text-label-secondary hover:bg-blue-50 rounded-ios-md"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveProvider}
                disabled={saving !== null}
                className="px-4 py-2 bg-accent-primary text-white rounded-ios-md hover:bg-accent-primary/90 disabled:opacity-50"
              >
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AdminOAuthSettings;

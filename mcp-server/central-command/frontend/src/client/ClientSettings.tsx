import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useClient } from './ClientContext';

interface OrgUser {
  id: string;
  email: string;
  name: string | null;
  role: 'owner' | 'admin' | 'viewer';
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

interface TransferStatus {
  id: string;
  status: 'pending' | 'approved' | 'rejected' | 'cancelled';
  target_partner_name: string;
  requested_at: string;
  reason: string;
}

interface BillingInfo {
  has_subscription: boolean;
  status: string;
  message?: string;
  plan?: {
    name: string;
    amount: number;
    currency: string;
    interval: string;
  };
  current_period_end?: string;
  cancel_at_period_end?: boolean;
  payment_method?: {
    type: string;
    brand: string;
    last4: string;
    exp_month: number;
    exp_year: number;
  };
}

interface Invoice {
  id: string;
  number: string;
  amount_due: number;
  amount_paid: number;
  currency: string;
  status: string;
  created: string;
  invoice_pdf: string;
  hosted_invoice_url: string;
}

export const ClientSettings: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, isLoading, user } = useClient();

  const [activeTab, setActiveTab] = useState<'users' | 'password' | 'transfer' | 'billing'>('users');
  const [users, setUsers] = useState<OrgUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Invite form
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<'admin' | 'viewer'>('viewer');
  const [inviting, setInviting] = useState(false);

  // Password form
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [savingPassword, setSavingPassword] = useState(false);

  // Transfer
  const [transferStatus, setTransferStatus] = useState<TransferStatus | null>(null);
  const [transferReason, setTransferReason] = useState('');
  const [requesting, setRequesting] = useState(false);

  // Billing
  const [billingInfo, setBillingInfo] = useState<BillingInfo | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [billingLoading, setBillingLoading] = useState(false);
  const [billingAvailable, setBillingAvailable] = useState(true);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/client/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchUsers();
      fetchTransferStatus();
      fetchBillingInfo();
    }
  }, [isAuthenticated]);

  // Check for billing query params (success/cancelled from Stripe checkout)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const billingStatus = params.get('billing');
    if (billingStatus === 'success') {
      setSuccess('Subscription activated successfully!');
      setActiveTab('billing');
      // Remove query params
      window.history.replaceState({}, '', '/client/settings');
      fetchBillingInfo();
    } else if (billingStatus === 'cancelled') {
      setActiveTab('billing');
      window.history.replaceState({}, '', '/client/settings');
    }
  }, []);

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/client/users', { credentials: 'include' });
      if (response.ok) {
        const data = await response.json();
        setUsers(data.users || []);
      }
    } catch (e) {
      console.error('Failed to fetch users:', e);
    } finally {
      setLoading(false);
    }
  };

  const fetchTransferStatus = async () => {
    try {
      const response = await fetch('/api/client/transfer/status', { credentials: 'include' });
      if (response.ok) {
        const data = await response.json();
        setTransferStatus(data.transfer || null);
      }
    } catch (e) {
      console.error('Failed to fetch transfer status:', e);
    }
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    setInviting(true);
    setError(null);
    setSuccess(null);

    try {
      const response = await fetch('/api/client/users/invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email: inviteEmail, role: inviteRole }),
      });

      if (response.ok) {
        setSuccess(`Invitation sent to ${inviteEmail}`);
        setInviteEmail('');
        fetchUsers();
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to send invitation');
      }
    } catch (e) {
      setError('Failed to send invitation');
    } finally {
      setInviting(false);
    }
  };

  const handleRemoveUser = async (userId: string) => {
    if (!confirm('Are you sure you want to remove this user?')) return;

    try {
      const response = await fetch(`/api/client/users/${userId}`, {
        method: 'DELETE',
        credentials: 'include',
      });

      if (response.ok) {
        setSuccess('User removed');
        fetchUsers();
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to remove user');
      }
    } catch (e) {
      setError('Failed to remove user');
    }
  };

  const handleChangeRole = async (userId: string, newRole: string) => {
    try {
      const response = await fetch(`/api/client/users/${userId}/role`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ role: newRole }),
      });

      if (response.ok) {
        setSuccess('Role updated');
        fetchUsers();
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to update role');
      }
    } catch (e) {
      setError('Failed to update role');
    }
  };

  const handleSetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    if (newPassword.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    setSavingPassword(true);
    setError(null);
    setSuccess(null);

    try {
      const response = await fetch('/api/client/password', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ password: newPassword }),
      });

      if (response.ok) {
        setSuccess('Password updated successfully');
        setNewPassword('');
        setConfirmPassword('');
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to update password');
      }
    } catch (e) {
      setError('Failed to update password');
    } finally {
      setSavingPassword(false);
    }
  };

  const handleRequestTransfer = async (e: React.FormEvent) => {
    e.preventDefault();
    setRequesting(true);
    setError(null);

    try {
      const response = await fetch('/api/client/transfer/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ reason: transferReason }),
      });

      if (response.ok) {
        setSuccess('Transfer request submitted');
        fetchTransferStatus();
        setTransferReason('');
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to submit transfer request');
      }
    } catch (e) {
      setError('Failed to submit transfer request');
    } finally {
      setRequesting(false);
    }
  };

  const handleCancelTransfer = async () => {
    try {
      const response = await fetch('/api/client/transfer/cancel', {
        method: 'POST',
        credentials: 'include',
      });

      if (response.ok) {
        setSuccess('Transfer request cancelled');
        setTransferStatus(null);
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to cancel transfer request');
      }
    } catch (e) {
      setError('Failed to cancel transfer request');
    }
  };

  const fetchBillingInfo = async () => {
    setBillingLoading(true);
    try {
      const response = await fetch('/api/client/billing', { credentials: 'include' });
      if (response.status === 503) {
        // Billing not available
        setBillingAvailable(false);
        return;
      }
      if (response.ok) {
        const data = await response.json();
        setBillingInfo(data);
        setBillingAvailable(true);
        // Also fetch invoices if subscription exists
        if (data.has_subscription) {
          fetchInvoices();
        }
      }
    } catch (e) {
      console.error('Failed to fetch billing:', e);
      setBillingAvailable(false);
    } finally {
      setBillingLoading(false);
    }
  };

  const fetchInvoices = async () => {
    try {
      const response = await fetch('/api/client/billing/invoices', { credentials: 'include' });
      if (response.ok) {
        const data = await response.json();
        setInvoices(data.invoices || []);
      }
    } catch (e) {
      console.error('Failed to fetch invoices:', e);
    }
  };

  const handleStartSubscription = async () => {
    setError(null);
    try {
      const response = await fetch('/api/client/billing/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({}),
      });

      if (response.ok) {
        const data = await response.json();
        // Redirect to Stripe Checkout
        window.location.href = data.checkout_url;
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to start checkout');
      }
    } catch (e) {
      setError('Failed to start checkout');
    }
  };

  const handleManageBilling = async () => {
    setError(null);
    try {
      const response = await fetch('/api/client/billing/portal', {
        method: 'POST',
        credentials: 'include',
      });

      if (response.ok) {
        const data = await response.json();
        // Redirect to Stripe Customer Portal
        window.location.href = data.portal_url;
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to open billing portal');
      }
    } catch (e) {
      setError('Failed to open billing portal');
    }
  };

  const canManageUsers = user?.role === 'owner' || user?.role === 'admin';

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="w-12 h-12 border-4 border-teal-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <Link to="/client/dashboard" className="p-2 text-gray-500 hover:text-gray-700 rounded-lg hover:bg-gray-100">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </Link>
              <h1 className="text-lg font-semibold text-gray-900">Account Settings</h1>
            </div>
          </div>
        </div>
      </header>

      {/* Main */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Alerts */}
        {error && (
          <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
            {error}
          </div>
        )}
        {success && (
          <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded-lg text-green-700">
            {success}
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-4 mb-6 border-b border-gray-200">
          <button
            onClick={() => setActiveTab('users')}
            className={`pb-2 px-1 font-medium ${
              activeTab === 'users'
                ? 'text-teal-600 border-b-2 border-teal-600'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Users
          </button>
          <button
            onClick={() => setActiveTab('password')}
            className={`pb-2 px-1 font-medium ${
              activeTab === 'password'
                ? 'text-teal-600 border-b-2 border-teal-600'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Password
          </button>
          {user?.role === 'owner' && (
            <button
              onClick={() => setActiveTab('transfer')}
              className={`pb-2 px-1 font-medium ${
                activeTab === 'transfer'
                  ? 'text-teal-600 border-b-2 border-teal-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              Transfer Provider
            </button>
          )}
          {user?.role === 'owner' && billingAvailable && (
            <button
              onClick={() => setActiveTab('billing')}
              className={`pb-2 px-1 font-medium ${
                activeTab === 'billing'
                  ? 'text-teal-600 border-b-2 border-teal-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              Billing
            </button>
          )}
        </div>

        {/* Users Tab */}
        {activeTab === 'users' && (
          <div className="space-y-6">
            {canManageUsers && (
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
                <h2 className="text-lg font-medium mb-4">Invite User</h2>
                <form onSubmit={handleInvite} className="flex gap-4">
                  <input
                    type="email"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    placeholder="Email address"
                    className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                    required
                  />
                  <select
                    value={inviteRole}
                    onChange={(e) => setInviteRole(e.target.value as 'admin' | 'viewer')}
                    className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  >
                    <option value="viewer">Viewer</option>
                    <option value="admin">Admin</option>
                  </select>
                  <button
                    type="submit"
                    disabled={inviting}
                    className="px-6 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 disabled:opacity-50"
                  >
                    {inviting ? 'Sending...' : 'Send Invite'}
                  </button>
                </form>
              </div>
            )}

            <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200">
                <h2 className="text-lg font-medium">Team Members</h2>
              </div>
              {loading ? (
                <div className="p-8 text-center">
                  <div className="w-8 h-8 border-4 border-teal-500 border-t-transparent rounded-full animate-spin mx-auto" />
                </div>
              ) : (
                <div className="divide-y divide-gray-200">
                  {users.map((u) => (
                    <div key={u.id} className="p-4 flex items-center justify-between">
                      <div>
                        <p className="font-medium text-gray-900">{u.name || u.email}</p>
                        <p className="text-sm text-gray-500">{u.email}</p>
                      </div>
                      <div className="flex items-center gap-4">
                        {canManageUsers && u.id !== user?.id && u.role !== 'owner' ? (
                          <select
                            value={u.role}
                            onChange={(e) => handleChangeRole(u.id, e.target.value)}
                            className="px-3 py-1 border border-gray-300 rounded text-sm"
                          >
                            <option value="viewer">Viewer</option>
                            <option value="admin">Admin</option>
                          </select>
                        ) : (
                          <span className="px-3 py-1 text-sm font-medium bg-gray-100 text-gray-700 rounded capitalize">
                            {u.role}
                          </span>
                        )}
                        {canManageUsers && u.id !== user?.id && u.role !== 'owner' && (
                          <button
                            onClick={() => handleRemoveUser(u.id)}
                            className="p-2 text-red-500 hover:bg-red-50 rounded"
                            title="Remove user"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Password Tab */}
        {activeTab === 'password' && (
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 max-w-md">
            <h2 className="text-lg font-medium mb-4">Set Password</h2>
            <p className="text-sm text-gray-500 mb-4">
              Set an optional password for convenient login. You can still use magic links.
            </p>
            <form onSubmit={handleSetPassword} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">New Password</label>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  required
                  minLength={8}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Confirm Password</label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  required
                  minLength={8}
                />
              </div>
              <button
                type="submit"
                disabled={savingPassword}
                className="w-full px-6 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 disabled:opacity-50"
              >
                {savingPassword ? 'Saving...' : 'Update Password'}
              </button>
            </form>
          </div>
        )}

        {/* Transfer Tab */}
        {activeTab === 'transfer' && user?.role === 'owner' && (
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 max-w-lg">
            <h2 className="text-lg font-medium mb-4">Request Provider Transfer</h2>
            {transferStatus ? (
              <div className="space-y-4">
                <div className="p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
                  <p className="font-medium text-yellow-800">Transfer Request Pending</p>
                  <p className="text-sm text-yellow-700 mt-1">
                    Status: {transferStatus.status}
                  </p>
                  <p className="text-sm text-yellow-700">
                    Requested: {new Date(transferStatus.requested_at).toLocaleDateString()}
                  </p>
                </div>
                {transferStatus.status === 'pending' && (
                  <button
                    onClick={handleCancelTransfer}
                    className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
                  >
                    Cancel Request
                  </button>
                )}
              </div>
            ) : (
              <>
                <p className="text-sm text-gray-500 mb-4">
                  Request to transfer your practice to a different MSP provider. Your compliance data will be preserved.
                </p>
                <form onSubmit={handleRequestTransfer} className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Reason for Transfer</label>
                    <textarea
                      value={transferReason}
                      onChange={(e) => setTransferReason(e.target.value)}
                      rows={3}
                      className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                      placeholder="Please explain why you want to transfer..."
                      required
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={requesting}
                    className="w-full px-6 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:opacity-50"
                  >
                    {requesting ? 'Submitting...' : 'Submit Transfer Request'}
                  </button>
                </form>
              </>
            )}
          </div>
        )}

        {/* Billing Tab */}
        {activeTab === 'billing' && user?.role === 'owner' && billingAvailable && (
          <div className="space-y-6">
            {billingLoading ? (
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
                <div className="w-8 h-8 border-4 border-teal-500 border-t-transparent rounded-full animate-spin mx-auto" />
              </div>
            ) : billingInfo?.has_subscription ? (
              <>
                {/* Current Plan */}
                <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
                  <h2 className="text-lg font-medium mb-4">Current Plan</h2>
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-2xl font-bold text-gray-900">
                        {billingInfo.plan?.name || 'OsirisCare Compliance'}
                      </p>
                      <p className="text-gray-500">
                        ${billingInfo.plan?.amount}/{billingInfo.plan?.interval}
                      </p>
                      {billingInfo.current_period_end && (
                        <p className="text-sm text-gray-500 mt-2">
                          {billingInfo.cancel_at_period_end ? 'Cancels' : 'Renews'} on{' '}
                          {new Date(billingInfo.current_period_end).toLocaleDateString()}
                        </p>
                      )}
                    </div>
                    <span className={`px-3 py-1 rounded-full text-sm font-medium ${
                      billingInfo.status === 'active'
                        ? 'bg-green-100 text-green-800'
                        : billingInfo.status === 'trialing'
                        ? 'bg-blue-100 text-blue-800'
                        : 'bg-yellow-100 text-yellow-800'
                    }`}>
                      {billingInfo.status === 'active' ? 'Active' :
                       billingInfo.status === 'trialing' ? 'Trial' :
                       billingInfo.status}
                    </span>
                  </div>

                  {billingInfo.payment_method && (
                    <div className="mt-4 pt-4 border-t border-gray-200">
                      <p className="text-sm text-gray-500">Payment Method</p>
                      <p className="font-medium">
                        {billingInfo.payment_method.brand.toUpperCase()} •••• {billingInfo.payment_method.last4}
                        <span className="text-gray-500 ml-2">
                          Expires {billingInfo.payment_method.exp_month}/{billingInfo.payment_method.exp_year}
                        </span>
                      </p>
                    </div>
                  )}

                  <button
                    onClick={handleManageBilling}
                    className="mt-4 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
                  >
                    Manage Subscription
                  </button>
                </div>

                {/* Invoices */}
                {invoices.length > 0 && (
                  <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                    <div className="px-6 py-4 border-b border-gray-200">
                      <h2 className="text-lg font-medium">Recent Invoices</h2>
                    </div>
                    <div className="divide-y divide-gray-200">
                      {invoices.map((invoice) => (
                        <div key={invoice.id} className="p-4 flex items-center justify-between">
                          <div>
                            <p className="font-medium text-gray-900">{invoice.number}</p>
                            <p className="text-sm text-gray-500">
                              {new Date(invoice.created).toLocaleDateString()}
                            </p>
                          </div>
                          <div className="flex items-center gap-4">
                            <span className={`px-2 py-1 text-xs rounded ${
                              invoice.status === 'paid'
                                ? 'bg-green-100 text-green-800'
                                : 'bg-yellow-100 text-yellow-800'
                            }`}>
                              {invoice.status}
                            </span>
                            <span className="font-medium">
                              ${invoice.amount_paid.toFixed(2)} {invoice.currency}
                            </span>
                            {invoice.invoice_pdf && (
                              <a
                                href={invoice.invoice_pdf}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-teal-600 hover:text-teal-700"
                              >
                                Download
                              </a>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              /* No subscription - show signup */
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 max-w-lg">
                <h2 className="text-lg font-medium mb-4">Subscribe to OsirisCare</h2>
                <p className="text-gray-500 mb-6">
                  Get full access to compliance monitoring, evidence management, and audit-ready reports.
                </p>

                <div className="bg-gray-50 rounded-lg p-4 mb-6">
                  <p className="text-2xl font-bold text-gray-900">
                    Starting at $200<span className="text-base font-normal text-gray-500">/month</span>
                  </p>
                  <ul className="mt-4 space-y-2 text-sm text-gray-600">
                    <li className="flex items-center gap-2">
                      <svg className="w-4 h-4 text-teal-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                      Continuous HIPAA compliance monitoring
                    </li>
                    <li className="flex items-center gap-2">
                      <svg className="w-4 h-4 text-teal-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                      Automatic evidence collection
                    </li>
                    <li className="flex items-center gap-2">
                      <svg className="w-4 h-4 text-teal-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                      7-year audit trail retention
                    </li>
                    <li className="flex items-center gap-2">
                      <svg className="w-4 h-4 text-teal-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                      Monthly compliance reports
                    </li>
                  </ul>
                </div>

                <button
                  onClick={handleStartSubscription}
                  className="w-full px-6 py-3 bg-teal-600 text-white rounded-lg hover:bg-teal-700 font-medium"
                >
                  Start Subscription
                </button>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
};

export default ClientSettings;

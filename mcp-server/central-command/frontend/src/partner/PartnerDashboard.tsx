import React, { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { QRCodeSVG } from 'qrcode.react';
import { usePartner } from './PartnerContext';
import { PartnerBilling } from './PartnerBilling';
import { PartnerComplianceSettings } from './PartnerComplianceSettings';
import { PartnerExceptionManagement } from './PartnerExceptionManagement';

interface Site {
  site_id: string;
  clinic_name: string;
  status: string;
  tier: string;
  onboarding_stage: string;
  appliance_count: number;
  last_checkin: string | null;
}

interface Provision {
  id: string;
  provision_code: string;
  qr_content: string;
  target_client_name: string | null;
  target_site_id: string | null;
  status: string;
  claimed_at: string | null;
  claimed_by_mac: string | null;
  expires_at: string;
  created_at: string;
}

export const PartnerDashboard: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { partner, apiKey, isAuthenticated, isLoading, logout } = usePartner();

  const [activeTab, setActiveTab] = useState<'sites' | 'provisions' | 'billing' | 'compliance' | 'exceptions'>('sites');

  // Handle billing redirect from Stripe
  useEffect(() => {
    const billingStatus = searchParams.get('billing');
    if (billingStatus === 'success' || billingStatus === 'canceled') {
      setActiveTab('billing');
    }
  }, [searchParams]);
  const [sites, setSites] = useState<Site[]>([]);
  const [provisions, setProvisions] = useState<Provision[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNewProvision, setShowNewProvision] = useState(false);
  const [newClientName, setNewClientName] = useState('');
  const [creating, setCreating] = useState(false);
  const [qrProvision, setQrProvision] = useState<Provision | null>(null);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/partner/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  useEffect(() => {
    if (isAuthenticated) {
      loadData();
    }
  }, [isAuthenticated]);

  const loadData = async () => {
    if (!isAuthenticated) return;
    setLoading(true);

    // Use API key header if available, otherwise use cookie auth
    const headers: HeadersInit = apiKey
      ? { 'X-API-Key': apiKey }
      : {};
    const fetchOptions: RequestInit = apiKey
      ? { headers }
      : { credentials: 'include' };

    try {
      const [sitesRes, provisionsRes] = await Promise.all([
        fetch('/api/partners/me/sites', fetchOptions),
        fetch('/api/partners/me/provisions', fetchOptions),
      ]);

      if (sitesRes.ok) {
        const data = await sitesRes.json();
        setSites(data.sites || []);
      }

      if (provisionsRes.ok) {
        const data = await provisionsRes.json();
        setProvisions(data.provisions || []);
      }
    } catch (e) {
      console.error('Failed to load data', e);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateProvision = async () => {
    if (!isAuthenticated || !newClientName.trim()) return;

    setCreating(true);
    try {
      const headers: HeadersInit = apiKey
        ? { 'Content-Type': 'application/json', 'X-API-Key': apiKey }
        : { 'Content-Type': 'application/json' };

      const response = await fetch('/api/partners/me/provisions', {
        method: 'POST',
        headers,
        credentials: apiKey ? undefined : 'include',
        body: JSON.stringify({
          target_client_name: newClientName.trim(),
          expires_days: 30,
        }),
      });

      if (response.ok) {
        setNewClientName('');
        setShowNewProvision(false);
        loadData();
      }
    } catch (e) {
      console.error('Failed to create provision', e);
    } finally {
      setCreating(false);
    }
  };

  const handleRevokeProvision = async (id: string) => {
    if (!isAuthenticated || !confirm('Revoke this provision code?')) return;

    try {
      const fetchOptions: RequestInit = apiKey
        ? { method: 'DELETE', headers: { 'X-API-Key': apiKey } }
        : { method: 'DELETE', credentials: 'include' };

      await fetch(`/api/partners/me/provisions/${id}`, fetchOptions);
      loadData();
    } catch (e) {
      console.error('Failed to revoke', e);
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'online':
      case 'active':
      case 'claimed':
        return 'bg-green-100 text-green-800';
      case 'pending':
        return 'bg-yellow-100 text-yellow-800';
      case 'offline':
      case 'expired':
      case 'revoked':
        return 'bg-red-100 text-red-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  const formatTime = (dateStr: string | null) => {
    if (!dateStr) return 'Never';
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / 60000);

    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
    return formatDate(dateStr);
  };

  if (isLoading || loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-4 border-indigo-500 border-t-transparent" />
      </div>
    );
  }

  if (!partner) {
    return null;
  }

  const primaryColor = partner.primary_color || '#4F46E5';

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            {partner.logo_url ? (
              <img src={partner.logo_url} alt={partner.brand_name} className="h-10" />
            ) : (
              <div
                className="w-10 h-10 rounded-lg flex items-center justify-center text-white font-bold"
                style={{ backgroundColor: primaryColor }}
              >
                {partner.brand_name.charAt(0)}
              </div>
            )}
            <div>
              <h1 className="text-xl font-semibold text-gray-900">{partner.brand_name}</h1>
              <p className="text-sm text-gray-500">Partner Dashboard</p>
            </div>
          </div>
          <button
            onClick={() => {
              logout();
              navigate('/partner/login');
            }}
            className="px-4 py-2 text-gray-600 hover:text-gray-900 transition"
          >
            Sign Out
          </button>
        </div>
      </header>

      {/* Stats */}
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="bg-white rounded-xl p-6 shadow-sm">
            <p className="text-sm text-gray-500 mb-1">Total Sites</p>
            <p className="text-3xl font-bold text-gray-900">{partner.site_count}</p>
          </div>
          <div className="bg-white rounded-xl p-6 shadow-sm">
            <p className="text-sm text-gray-500 mb-1">Pending Provisions</p>
            <p className="text-3xl font-bold text-yellow-600">{partner.provisions.pending}</p>
          </div>
          <div className="bg-white rounded-xl p-6 shadow-sm">
            <p className="text-sm text-gray-500 mb-1">Claimed Provisions</p>
            <p className="text-3xl font-bold text-green-600">{partner.provisions.claimed}</p>
          </div>
          <div className="bg-white rounded-xl p-6 shadow-sm">
            <p className="text-sm text-gray-500 mb-1">Revenue Share</p>
            <p className="text-3xl font-bold" style={{ color: primaryColor }}>
              {partner.revenue_share_percent}%
            </p>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex gap-4 border-b">
          <button
            onClick={() => setActiveTab('sites')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px ${
              activeTab === 'sites'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Sites ({sites.length})
          </button>
          <button
            onClick={() => setActiveTab('provisions')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px ${
              activeTab === 'provisions'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Provision Codes ({provisions.length})
          </button>
          <button
            onClick={() => setActiveTab('billing')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px ${
              activeTab === 'billing'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Billing
          </button>
          <button
            onClick={() => setActiveTab('compliance')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px ${
              activeTab === 'compliance'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Compliance
          </button>
          <button
            onClick={() => setActiveTab('exceptions')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px ${
              activeTab === 'exceptions'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Exceptions
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-7xl mx-auto px-6 py-6">
        {activeTab === 'sites' && (
          <div className="bg-white rounded-xl shadow-sm overflow-hidden">
            {sites.length === 0 ? (
              <div className="p-12 text-center">
                <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                </div>
                <h3 className="text-lg font-medium text-gray-900 mb-2">No Sites Yet</h3>
                <p className="text-gray-500 mb-4">
                  Create a provision code and use it to onboard your first client.
                </p>
                <button
                  onClick={() => {
                    setActiveTab('provisions');
                    setShowNewProvision(true);
                  }}
                  className="px-4 py-2 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 transition"
                >
                  Create Provision Code
                </button>
              </div>
            ) : (
              <table className="w-full">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Site</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tier</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Appliances</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Last Check-in</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {sites.map((site) => (
                    <tr key={site.site_id} className="hover:bg-gray-50">
                      <td className="px-6 py-4">
                        <div>
                          <p className="font-medium text-gray-900">{site.clinic_name}</p>
                          <p className="text-sm text-gray-500">{site.site_id}</p>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <span className={`px-2 py-1 text-xs font-medium rounded-full ${getStatusColor(site.status)}`}>
                          {site.status}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-600 capitalize">{site.tier}</td>
                      <td className="px-6 py-4 text-sm text-gray-600">{site.appliance_count}</td>
                      <td className="px-6 py-4 text-sm text-gray-600">{formatTime(site.last_checkin)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {activeTab === 'provisions' && (
          <div>
            {/* New Provision Button */}
            <div className="mb-4 flex justify-end">
              <button
                onClick={() => setShowNewProvision(true)}
                className="px-4 py-2 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 transition flex items-center gap-2"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                New Provision Code
              </button>
            </div>

            {/* New Provision Modal */}
            {showNewProvision && (
              <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
                <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Create Provision Code</h3>
                  <div className="mb-4">
                    <label className="block text-sm font-medium text-gray-700 mb-1">Client Name</label>
                    <input
                      type="text"
                      value={newClientName}
                      onChange={(e) => setNewClientName(e.target.value)}
                      placeholder="e.g., Scranton Family Practice"
                      className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      This will be the default name when the appliance claims this code.
                    </p>
                  </div>
                  <div className="flex gap-3 justify-end">
                    <button
                      onClick={() => {
                        setShowNewProvision(false);
                        setNewClientName('');
                      }}
                      className="px-4 py-2 text-gray-600 hover:text-gray-900 transition"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleCreateProvision}
                      disabled={creating || !newClientName.trim()}
                      className="px-4 py-2 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition"
                    >
                      {creating ? 'Creating...' : 'Create Code'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* QR Code Modal */}
            {qrProvision && (
              <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
                <div className="bg-white rounded-2xl p-8 w-full max-w-md shadow-xl">
                  <div className="text-center">
                    <h3 className="text-lg font-semibold text-gray-900 mb-2">Provision QR Code</h3>
                    <p className="text-sm text-gray-500 mb-6">
                      {qrProvision.target_client_name || 'New Appliance'}
                    </p>

                    {/* QR Code */}
                    <div className="bg-white p-4 rounded-xl border-2 border-gray-200 inline-block mb-6">
                      <QRCodeSVG
                        value={qrProvision.qr_content}
                        size={200}
                        level="M"
                        includeMargin={true}
                      />
                    </div>

                    {/* Manual Code */}
                    <div className="mb-6">
                      <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">Manual Entry Code</p>
                      <code className="px-4 py-2 bg-gray-100 rounded-lg text-lg font-mono font-bold tracking-wider">
                        {qrProvision.provision_code}
                      </code>
                    </div>

                    {/* Instructions */}
                    <div className="text-left bg-gray-50 rounded-lg p-4 mb-6">
                      <p className="text-sm font-medium text-gray-700 mb-2">To provision an appliance:</p>
                      <ol className="text-sm text-gray-600 space-y-1 list-decimal list-inside">
                        <li>Boot the OsirisCare appliance</li>
                        <li>Scan this QR code or enter the code manually</li>
                        <li>The appliance will register automatically</li>
                      </ol>
                    </div>

                    {/* Expiration */}
                    <p className="text-xs text-gray-400 mb-4">
                      Expires: {formatDate(qrProvision.expires_at)}
                    </p>

                    {/* Actions */}
                    <div className="flex gap-3 justify-center">
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(qrProvision.provision_code);
                        }}
                        className="px-4 py-2 text-indigo-600 hover:text-indigo-800 font-medium transition flex items-center gap-2"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                        </svg>
                        Copy Code
                      </button>
                      <button
                        onClick={() => setQrProvision(null)}
                        className="px-4 py-2 bg-gray-900 text-white font-medium rounded-lg hover:bg-gray-800 transition"
                      >
                        Done
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Provisions Table */}
            <div className="bg-white rounded-xl shadow-sm overflow-hidden">
              {provisions.length === 0 ? (
                <div className="p-12 text-center">
                  <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
                    <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01M16 20h4M4 12h4m12 0h.01M5 8h2a1 1 0 001-1V5a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1zm12 0h2a1 1 0 001-1V5a1 1 0 00-1-1h-2a1 1 0 00-1 1v2a1 1 0 001 1zM5 20h2a1 1 0 001-1v-2a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1z" />
                    </svg>
                  </div>
                  <h3 className="text-lg font-medium text-gray-900 mb-2">No Provision Codes</h3>
                  <p className="text-gray-500">Create a provision code to onboard new appliances.</p>
                </div>
              ) : (
                <table className="w-full">
                  <thead className="bg-gray-50 border-b">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Code</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Client</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Expires</th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {provisions.map((provision) => (
                      <tr key={provision.id} className="hover:bg-gray-50">
                        <td className="px-6 py-4">
                          <code className="px-2 py-1 bg-gray-100 rounded text-sm font-mono">
                            {provision.provision_code}
                          </code>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-600">
                          {provision.target_client_name || '-'}
                        </td>
                        <td className="px-6 py-4">
                          <span className={`px-2 py-1 text-xs font-medium rounded-full ${getStatusColor(provision.status)}`}>
                            {provision.status}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-600">{formatDate(provision.created_at)}</td>
                        <td className="px-6 py-4 text-sm text-gray-600">{formatDate(provision.expires_at)}</td>
                        <td className="px-6 py-4 text-right">
                          <div className="flex items-center justify-end gap-2">
                            {provision.status === 'pending' && (
                              <>
                                <button
                                  onClick={() => setQrProvision(provision)}
                                  className="text-indigo-600 hover:text-indigo-800 text-sm font-medium flex items-center gap-1"
                                >
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01M16 20h4M4 12h4m12 0h.01M5 8h2a1 1 0 001-1V5a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1zm12 0h2a1 1 0 001-1V5a1 1 0 00-1-1h-2a1 1 0 00-1 1v2a1 1 0 001 1zM5 20h2a1 1 0 001-1v-2a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1z" />
                                  </svg>
                                  QR
                                </button>
                                <button
                                  onClick={() => handleRevokeProvision(provision.id)}
                                  className="text-red-600 hover:text-red-800 text-sm font-medium"
                                >
                                  Revoke
                                </button>
                              </>
                            )}
                            {provision.status === 'claimed' && (
                              <span className="text-sm text-gray-500">
                                {provision.claimed_by_mac}
                              </span>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}

        {activeTab === 'billing' && (
          <PartnerBilling />
        )}

        {activeTab === 'compliance' && (
          <PartnerComplianceSettings />
        )}

        {activeTab === 'exceptions' && (
          <PartnerExceptionManagement
            sites={sites.map(s => ({ id: s.site_id, name: s.clinic_name }))}
          />
        )}
      </div>
    </div>
  );
};

export default PartnerDashboard;

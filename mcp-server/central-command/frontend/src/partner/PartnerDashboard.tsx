import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { QRCodeSVG } from 'qrcode.react';
import { usePartner } from './PartnerContext';
import { PartnerBilling } from './PartnerBilling';
import PartnerCommission from './PartnerCommission';
import { PartnerAgreements } from './PartnerAgreements';
import { PartnerInvites } from './PartnerInvites';
import { csrfHeaders } from '../utils/csrf';
import { PartnerComplianceSettings } from './PartnerComplianceSettings';
import { PartnerExceptionManagement } from './PartnerExceptionManagement';
import { PartnerLearning } from './PartnerLearning';
import { PartnerEscalations } from './PartnerEscalations';
import { PartnerHomeDashboard } from './PartnerHomeDashboard';
import { PartnerDriftConfig } from './PartnerDriftConfig';
import { PartnerOnboarding } from './PartnerOnboarding';
import { PartnerSSOConfig } from './PartnerSSOConfig';
import { PartnerSearchOmnibox } from './PartnerSearchOmnibox';
import { PartnerWeeklyRollup } from './PartnerWeeklyRollup';
import { InfoTip, WelcomeModal } from '../components/shared';
import { formatTimeAgo } from '../constants';

interface Site {
  site_id: string;
  clinic_name: string;
  status: string;
  tier: string;
  onboarding_stage: string;
  appliance_count: number;
  last_checkin: string | null;
  agent_compliance_rate?: number;
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

  const [activeTab, setActiveTab] = useState<'sites' | 'onboarding' | 'provisions' | 'agreements' | 'invites' | 'billing' | 'commission' | 'compliance' | 'exceptions' | 'escalations' | 'learning' | 'sso' | 'inventory'>('sites');
  const [ssoConfigSite, setSsoConfigSite] = useState<{ id: string; name: string } | null>(null);

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
  const [showBulkProvision, setShowBulkProvision] = useState(false);
  const [bulkCsvText, setBulkCsvText] = useState('');
  const [bulkCreating, setBulkCreating] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkResult, setBulkResult] = useState<{ count: number } | null>(null);
  const [creating, setCreating] = useState(false);
  const [qrProvision, setQrProvision] = useState<Provision | null>(null);
  const [driftConfigSite, setDriftConfigSite] = useState<{ id: string; name: string } | null>(null);
  const [showWelcome, setShowWelcome] = useState(() => !localStorage.getItem('osiriscare_partner_onboarded'));
  const [revokeConfirmId, setRevokeConfirmId] = useState<string | null>(null);

  // Memoize portfolio health computation (avoids re-filtering + reduce on every render)
  const portfolioAvg = useMemo(() => {
    const rated = sites.filter(s => typeof s.agent_compliance_rate === 'number');
    return rated.length > 0
      ? Math.round(rated.reduce((sum, s) => sum + (s.agent_compliance_rate ?? 0), 0) / rated.length)
      : null;
  }, [sites]);

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
      const response = await fetch('/api/partners/me/provisions', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...csrfHeaders(),
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
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

  const handleBulkProvision = async () => {
    if (!isAuthenticated) return;
    const lines = bulkCsvText
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('#'));
    if (lines.length === 0) {
      setBulkError('Paste at least one client name (one per line, CSV first column).');
      return;
    }
    if (lines.length > 100) {
      setBulkError('Max 100 rows per bulk upload. Split into multiple batches.');
      return;
    }
    const entries = lines.map((line) => {
      // First column = client_name. Additional columns reserved for future use.
      const cols = line.split(',').map((c) => c.trim());
      return { client_name: cols[0] };
    });
    setBulkCreating(true);
    setBulkError(null);
    setBulkResult(null);
    try {
      const response = await fetch('/api/partners/me/provisions/bulk', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...csrfHeaders(),
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
        body: JSON.stringify({ entries, expires_days: 30 }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        setBulkError(body.detail || `Server returned ${response.status}`);
        return;
      }
      const data = await response.json();
      setBulkResult({ count: data.count || entries.length });
      setBulkCsvText('');
      loadData();
    } catch (e) {
      setBulkError(e instanceof Error ? e.message : 'Bulk upload failed');
    } finally {
      setBulkCreating(false);
    }
  };

  const handleRevokeProvision = async (id: string) => {
    if (!isAuthenticated) return;

    try {
      await fetch(`/api/partners/me/provisions/${id}`, {
        method: 'DELETE',
        credentials: 'include',
        headers: {
          ...csrfHeaders(),
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
      });
      setRevokeConfirmId(null);
      loadData();
    } catch (e) {
      console.error('Failed to revoke', e);
      setRevokeConfirmId(null);
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
        return 'bg-slate-100 text-slate-800';
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


  if (isLoading || loading) {
    return (
      <div className="min-h-screen bg-slate-50/80 flex items-center justify-center">
        <div className="text-center">
          <div className="w-12 h-12 rounded-2xl mx-auto mb-4 flex items-center justify-center animate-pulse-soft" style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)' }}>
            <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
            </svg>
          </div>
          <p className="text-slate-500 text-sm">Loading dashboard...</p>
        </div>
      </div>
    );
  }

  if (!partner) {
    return null;
  }

  const primaryColor = partner.primary_color || '#4F46E5';

  return (
    <div className="min-h-screen bg-slate-50/80 page-enter">
      <WelcomeModal
        isOpen={showWelcome}
        onClose={() => {
          setShowWelcome(false);
          localStorage.setItem('osiriscare_partner_onboarded', 'true');
        }}
        portalType="partner"
      />
      {/* Global Cmd-K search — attaches keydown listener globally */}
      <PartnerSearchOmnibox />
      {/* Header */}
      <header className="sticky top-0 z-30 border-b border-slate-200/60" style={{ background: 'rgba(255,255,255,0.82)', backdropFilter: 'blur(20px) saturate(180%)', WebkitBackdropFilter: 'blur(20px) saturate(180%)' }}>
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            {partner.logo_url ? (
              <img src={partner.logo_url} alt={partner.brand_name} className="h-10" />
            ) : (
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center text-white font-bold text-lg"
                style={{ background: `linear-gradient(135deg, ${primaryColor} 0%, #7C3AED 100%)`, boxShadow: `0 2px 10px ${primaryColor}40` }}
              >
                {partner.brand_name.charAt(0)}
              </div>
            )}
            <div>
              <h1 className="text-lg font-semibold text-slate-900 tracking-tight">{partner.brand_name}</h1>
              <p className="text-xs text-slate-500">Partner Dashboard</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                localStorage.removeItem('osiriscare_partner_onboarded');
                setShowWelcome(true);
              }}
              className="px-3 py-2 text-sm text-slate-400 hover:text-indigo-600 rounded-lg hover:bg-indigo-50 transition"
              title="Show Welcome Guide"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9 5.25h.008v.008H12v-.008z" />
              </svg>
            </button>
            <button
              onClick={() => {
                logout();
                navigate('/partner/login');
              }}
              className="px-4 py-2 text-sm text-slate-500 hover:text-indigo-600 rounded-lg hover:bg-indigo-50 transition"
            >
              Sign Out
            </button>
          </div>
        </div>
      </header>

      {/* Partner hero dashboard (Session 206 round-table P0) */}
      <PartnerHomeDashboard />

      {/* P2: weekly rollup table — entire book-of-business in one view */}
      <PartnerWeeklyRollup />

      {/* Stats */}
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          <div className="bg-white rounded-2xl p-6 shadow-sm border border-slate-100 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, rgba(79,70,229,0.12) 0%, rgba(124,58,237,0.08) 100%)' }}>
                <svg className="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5" /></svg>
              </div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Total Sites<InfoTip text="Client locations actively being monitored by your organization." /></p>
            </div>
            <p className="text-3xl font-bold text-slate-900 tabular-nums">{partner.site_count}</p>
          </div>
          <div className="bg-white rounded-2xl p-6 shadow-sm border border-slate-100 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, rgba(234,179,8,0.12) 0%, rgba(245,158,11,0.08) 100%)' }}>
                <svg className="w-5 h-5 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
              </div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Pending<InfoTip text="Provision codes created but not yet claimed by an appliance." /></p>
            </div>
            <p className="text-3xl font-bold text-yellow-600 tabular-nums">{partner.provisions.pending}</p>
          </div>
          <div className="bg-white rounded-2xl p-6 shadow-sm border border-slate-100 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, rgba(34,197,94,0.12) 0%, rgba(22,163,74,0.08) 100%)' }}>
                <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
              </div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Claimed<InfoTip text="Provision codes successfully activated by appliances on-site." /></p>
            </div>
            <p className="text-3xl font-bold text-green-600 tabular-nums">{partner.provisions.claimed}</p>
          </div>
          <div className="bg-white rounded-2xl p-6 shadow-sm border border-slate-100 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `linear-gradient(135deg, ${primaryColor}1F 0%, ${primaryColor}14 100%)` }}>
                <svg className="w-5 h-5" style={{ color: primaryColor }} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
              </div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Revenue Share<InfoTip text="Your percentage of recurring revenue from managed client sites." /></p>
            </div>
            <p className="text-3xl font-bold tabular-nums" style={{ color: primaryColor }}>
              {partner.revenue_share_percent}%
            </p>
          </div>
          <div className="bg-white rounded-2xl p-6 shadow-sm border border-slate-100 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, rgba(16,185,129,0.12) 0%, rgba(5,150,105,0.08) 100%)' }}>
                <svg className="w-5 h-5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
              </div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Portfolio Health<InfoTip text="Average compliance score across all active client sites." /></p>
            </div>
            {(() => {
              const avg = portfolioAvg;
              const color = avg === null ? 'text-slate-400' : avg >= 90 ? 'text-emerald-600' : avg >= 70 ? 'text-yellow-600' : 'text-red-600';
              return (
                <p className={`text-3xl font-bold tabular-nums ${color}`}>
                  {avg !== null ? `${avg}%` : '--'}
                </p>
              );
            })()}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex gap-4 border-b overflow-x-auto -mx-6 px-6 min-w-0">
          <button
            onClick={() => setActiveTab('sites')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'sites'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Sites ({sites.length})
          </button>
          <button
            onClick={() => setActiveTab('onboarding')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'onboarding'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Onboarding
          </button>
          <button
            onClick={() => setActiveTab('provisions')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'provisions'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Provision Codes ({provisions.length})
          </button>
          <button
            onClick={() => setActiveTab('agreements')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'agreements'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Agreements
          </button>
          <button
            onClick={() => setActiveTab('invites')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'invites'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Invites
          </button>
          <button
            onClick={() => setActiveTab('billing')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'billing'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Billing
          </button>
          <button
            onClick={() => setActiveTab('commission')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'commission'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Commission
          </button>
          <button
            onClick={() => setActiveTab('compliance')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'compliance'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Compliance
          </button>
          <button
            onClick={() => setActiveTab('exceptions')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'exceptions'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Exceptions
          </button>
          <button
            onClick={() => setActiveTab('escalations')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'escalations'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Escalations
          </button>
          <button
            onClick={() => setActiveTab('learning')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'learning'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Learning
          </button>
          <button
            onClick={() => setActiveTab('sso')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'sso'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            SSO
          </button>
          <button
            onClick={() => setActiveTab('inventory')}
            className={`px-4 py-3 font-medium transition border-b-2 -mb-px whitespace-nowrap min-h-[44px] ${
              activeTab === 'inventory'
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-slate-500 hover:text-indigo-600'
            }`}
          >
            Inventory
          </button>
          <button
            onClick={() => navigate('/partner/security')}
            className="px-4 py-3 font-medium transition border-b-2 -mb-px border-transparent text-slate-500 hover:text-indigo-600 whitespace-nowrap min-h-[44px]"
          >
            Security
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-7xl mx-auto px-6 py-6">
        {activeTab === 'sites' && driftConfigSite && (
          <PartnerDriftConfig
            siteId={driftConfigSite.id}
            siteName={driftConfigSite.name}
            onBack={() => setDriftConfigSite(null)}
          />
        )}

        {activeTab === 'sites' && !driftConfigSite && (
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
            {sites.length === 0 ? (
              <div className="p-12 text-center">
                <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                </div>
                <h3 className="text-lg font-medium text-slate-900 mb-2">No Sites Yet</h3>
                <p className="text-slate-500 mb-4">
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
              <>
                {/* Mobile card view */}
                <div className="md:hidden space-y-2 p-4">
                  {sites.map((site) => (
                    <div
                      key={site.site_id}
                      className="rounded-xl border border-slate-200 p-4 hover:bg-indigo-50/50 transition cursor-pointer"
                      onClick={() => setDriftConfigSite({ id: site.site_id, name: site.clinic_name })}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <h3 className="text-sm font-semibold text-slate-900 truncate">{site.clinic_name}</h3>
                        <span className={`px-2 py-1 text-xs font-medium rounded-full flex-shrink-0 ml-2 ${getStatusColor(site.status)}`}>
                          {site.status}
                        </span>
                      </div>
                      <div className="grid grid-cols-2 gap-2 text-xs text-slate-500">
                        <span>Appliances: {site.appliance_count}</span>
                        <span>Last: {formatTimeAgo(site.last_checkin)}</span>
                      </div>
                    </div>
                  ))}
                </div>
                {/* Desktop table view */}
                <div className="hidden md:block">
                  <table className="w-full">
                    <thead className="bg-slate-50 border-b">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Site</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Status</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Tier<InfoTip text="Standard: basic monitoring. Professional: full auto-healing. Enterprise: custom rules and dedicated support." /></th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Appliances</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Last Check-in</th>
                        <th className="px-6 py-3 text-right text-xs font-medium text-slate-500 uppercase">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200">
                      {sites.map((site) => (
                        <tr key={site.site_id} className="hover:bg-indigo-50/50">
                          <td className="px-6 py-4">
                            <div>
                              <p className="font-medium text-slate-900">{site.clinic_name}</p>
                            </div>
                          </td>
                          <td className="px-6 py-4">
                            <span className={`px-2 py-1 text-xs font-medium rounded-full ${getStatusColor(site.status)}`}>
                              {site.status}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-sm text-slate-600 capitalize">{site.tier}</td>
                          <td className="px-6 py-4 text-sm text-slate-600">{site.appliance_count}</td>
                          <td className="px-6 py-4 text-sm text-slate-600">{formatTimeAgo(site.last_checkin)}</td>
                          <td className="px-6 py-4 text-right">
                            <button
                              onClick={() => setDriftConfigSite({ id: site.site_id, name: site.clinic_name })}
                              className="text-indigo-600 hover:text-indigo-800 text-sm font-medium transition"
                            >
                              Security Checks
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )}

        {activeTab === 'onboarding' && (
          <PartnerOnboarding />
        )}

        {activeTab === 'provisions' && (
          <div>
            {/* New Provision + Bulk Upload Buttons */}
            <div className="mb-4 flex justify-end gap-2">
              <button
                onClick={() => { setShowBulkProvision(true); setBulkError(null); setBulkResult(null); }}
                className="px-4 py-2 bg-slate-100 text-slate-700 font-medium rounded-lg hover:bg-slate-200 transition flex items-center gap-2 border border-slate-300"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
                Bulk Upload (CSV)
              </button>
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

            {/* Bulk Upload Modal */}
            {showBulkProvision && (
              <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 modal-backdrop">
                <div className="bg-white rounded-2xl p-6 w-full max-w-lg shadow-xl">
                  <h3 className="text-lg font-semibold text-slate-900 mb-2">Bulk Provision Codes</h3>
                  <p className="text-sm text-slate-600 mb-3">
                    One client per line. CSV format — first column is client name. Max 100 rows.
                  </p>
                  <textarea
                    value={bulkCsvText}
                    onChange={(e) => setBulkCsvText(e.target.value)}
                    placeholder={"Scranton Family Practice\nWilkes-Barre Dental\nClarks Summit Urgent Care"}
                    rows={8}
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono text-sm focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                  />
                  {bulkError && (
                    <div className="mt-2 text-sm text-rose-600 bg-rose-50 border border-rose-200 rounded px-3 py-2">
                      {bulkError}
                    </div>
                  )}
                  {bulkResult && (
                    <div className="mt-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-3 py-2">
                      Created {bulkResult.count} provision code{bulkResult.count === 1 ? '' : 's'}. See the list below.
                    </div>
                  )}
                  <div className="mt-4 flex gap-3 justify-end">
                    <button
                      onClick={() => { setShowBulkProvision(false); setBulkCsvText(''); setBulkError(null); setBulkResult(null); }}
                      className="px-4 py-2 text-slate-600 hover:text-slate-900 transition"
                    >
                      Close
                    </button>
                    <button
                      onClick={handleBulkProvision}
                      disabled={bulkCreating || !bulkCsvText.trim()}
                      className="px-4 py-2 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition"
                    >
                      {bulkCreating ? 'Creating…' : 'Create Codes'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* New Provision Modal */}
            {showNewProvision && (
              <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 modal-backdrop">
                <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
                  <h3 className="text-lg font-semibold text-slate-900 mb-4">Create Provision Code</h3>
                  <div className="mb-4">
                    <label className="block text-sm font-medium text-slate-700 mb-1">Client Name</label>
                    <input
                      type="text"
                      value={newClientName}
                      onChange={(e) => setNewClientName(e.target.value)}
                      placeholder="e.g., Scranton Family Practice"
                      className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      This will be the default name when the appliance claims this code.
                    </p>
                  </div>
                  <div className="flex gap-3 justify-end">
                    <button
                      onClick={() => {
                        setShowNewProvision(false);
                        setNewClientName('');
                      }}
                      className="px-4 py-2 text-slate-600 hover:text-slate-900 transition"
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
              <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 modal-backdrop">
                <div className="bg-white rounded-2xl p-8 w-full max-w-md shadow-xl">
                  <div className="text-center">
                    <h3 className="text-lg font-semibold text-slate-900 mb-2">Provision QR Code</h3>
                    <p className="text-sm text-slate-500 mb-6">
                      {qrProvision.target_client_name || 'New Appliance'}
                    </p>

                    {/* QR Code */}
                    <div className="bg-white p-4 rounded-xl border-2 border-slate-200 inline-block mb-6">
                      <QRCodeSVG
                        value={qrProvision.qr_content}
                        size={200}
                        level="M"
                        includeMargin={true}
                      />
                    </div>

                    {/* Manual Code */}
                    <div className="mb-6">
                      <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">Manual Entry Code</p>
                      <code className="px-4 py-2 bg-slate-100 rounded-lg text-lg font-mono font-bold tracking-wider">
                        {qrProvision.provision_code}
                      </code>
                    </div>

                    {/* Instructions */}
                    <div className="text-left bg-slate-50 rounded-lg p-4 mb-6">
                      <p className="text-sm font-medium text-slate-700 mb-2">To provision an appliance:</p>
                      <ol className="text-sm text-slate-600 space-y-1 list-decimal list-inside">
                        <li>Boot the OsirisCare appliance</li>
                        <li>Scan this QR code or enter the code manually</li>
                        <li>The appliance will register automatically</li>
                      </ol>
                    </div>

                    {/* Expiration */}
                    <p className="text-xs text-slate-400 mb-4">
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
                        className="px-4 py-2 bg-slate-900 text-white font-medium rounded-lg hover:bg-slate-800 transition"
                      >
                        Done
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Provisions Table */}
            <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
              {provisions.length === 0 ? (
                <div className="p-12 text-center">
                  <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
                    <svg className="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01M16 20h4M4 12h4m12 0h.01M5 8h2a1 1 0 001-1V5a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1zm12 0h2a1 1 0 001-1V5a1 1 0 00-1-1h-2a1 1 0 00-1 1v2a1 1 0 001 1zM5 20h2a1 1 0 001-1v-2a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1z" />
                    </svg>
                  </div>
                  <h3 className="text-lg font-medium text-slate-900 mb-2">No Provision Codes</h3>
                  <p className="text-slate-500">Create a provision code to onboard new appliances.</p>
                </div>
              ) : (
                <table className="w-full">
                  <thead className="bg-slate-50 border-b">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Code</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Client</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Created</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Expires</th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-slate-500 uppercase">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200">
                    {provisions.map((provision) => (
                      <tr key={provision.id} className="hover:bg-indigo-50/50">
                        <td className="px-6 py-4">
                          <code className="px-2 py-1 bg-slate-100 rounded text-sm font-mono">
                            {provision.provision_code}
                          </code>
                        </td>
                        <td className="px-6 py-4 text-sm text-slate-600">
                          {provision.target_client_name || '-'}
                        </td>
                        <td className="px-6 py-4">
                          <span className={`px-2 py-1 text-xs font-medium rounded-full ${getStatusColor(provision.status)}`}>
                            {provision.status}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm text-slate-600">{formatDate(provision.created_at)}</td>
                        <td className="px-6 py-4 text-sm text-slate-600">{formatDate(provision.expires_at)}</td>
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
                                {revokeConfirmId === provision.id ? (
                                  <span className="flex items-center gap-1">
                                    <span className="text-xs text-slate-500">Revoke?</span>
                                    <button
                                      onClick={() => handleRevokeProvision(provision.id)}
                                      className="text-red-600 hover:text-red-800 text-xs font-semibold"
                                    >
                                      Yes
                                    </button>
                                    <button
                                      onClick={() => setRevokeConfirmId(null)}
                                      className="text-slate-500 hover:text-slate-700 text-xs font-medium"
                                    >
                                      No
                                    </button>
                                  </span>
                                ) : (
                                  <button
                                    onClick={() => setRevokeConfirmId(provision.id)}
                                    className="text-red-600 hover:text-red-800 text-sm font-medium"
                                  >
                                    Revoke
                                  </button>
                                )}
                              </>
                            )}
                            {provision.status === 'claimed' && (
                              <span className="inline-flex items-center gap-1 text-sm text-green-600">
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                </svg>
                                Claimed
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

        {activeTab === 'agreements' && (
          <PartnerAgreements />
        )}

        {activeTab === 'invites' && (
          <PartnerInvites onGoToAgreements={() => setActiveTab('agreements')} />
        )}

        {activeTab === 'billing' && (
          <PartnerBilling />
        )}

        {activeTab === 'commission' && (
          <PartnerCommission />
        )}

        {activeTab === 'compliance' && (
          <PartnerComplianceSettings />
        )}

        {activeTab === 'exceptions' && (
          <PartnerExceptionManagement
            sites={sites.map(s => ({ id: s.site_id, name: s.clinic_name }))}
          />
        )}

        {activeTab === 'escalations' && (
          <PartnerEscalations />
        )}

        {activeTab === 'learning' && (
          <PartnerLearning />
        )}

        {activeTab === 'sso' && ssoConfigSite && (
          <PartnerSSOConfig
            orgId={ssoConfigSite.id}
            orgName={ssoConfigSite.name}
            onBack={() => setSsoConfigSite(null)}
          />
        )}

        {activeTab === 'sso' && !ssoConfigSite && (
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
            {sites.length === 0 ? (
              <div className="p-12 text-center">
                <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                  </svg>
                </div>
                <h3 className="text-lg font-medium text-slate-900 mb-2">No Sites Available</h3>
                <p className="text-slate-500">Add sites first, then configure SSO for their organizations.</p>
              </div>
            ) : (
              <div>
                <div className="px-6 py-4 border-b border-slate-100">
                  <h3 className="font-semibold text-slate-900">Select a Site to Configure SSO</h3>
                  <p className="text-sm text-slate-500 mt-1">Configure single sign-on for client organizations.</p>
                </div>
                <div className="divide-y divide-slate-100">
                  {sites.map((site) => (
                    <button
                      key={site.site_id}
                      onClick={() => setSsoConfigSite({ id: site.site_id, name: site.clinic_name })}
                      className="w-full px-6 py-4 flex items-center justify-between hover:bg-indigo-50/50 transition-colors text-left"
                    >
                      <div>
                        <p className="font-medium text-slate-900">{site.clinic_name}</p>
                      </div>
                      <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === 'inventory' && (
          <PartnerInventory apiKey={apiKey} />
        )}
      </div>
    </div>
  );
};

/** Partner org inventory — devices, workstations, agents, witnesses across all orgs */
const PartnerInventory: React.FC<{ apiKey: string | null }> = ({ apiKey }) => {
  const [orgs, setOrgs] = React.useState<Array<{ id: string; name: string; site_count: number }>>([]);
  const [selectedOrg, setSelectedOrg] = React.useState<string>('');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [data, setData] = React.useState<Record<string, any>>({});
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (apiKey) headers['X-API-Key'] = apiKey;
    fetch('/api/partners/me/orgs', { credentials: 'same-origin', headers })
      .then(r => r.json())
      .then(d => {
        const list = d.organizations || [];
        setOrgs(list);
        if (list.length > 0) setSelectedOrg(list[0].id);
      })
      .catch(() => {});
  }, [apiKey]);

  React.useEffect(() => {
    if (!selectedOrg) return;
    setLoading(true);
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (apiKey) headers['X-API-Key'] = apiKey;
    const base = `/api/partners/me/orgs/${selectedOrg}`;
    Promise.all([
      fetch(`${base}/devices`, { credentials: 'same-origin', headers }).then(r => r.json()).catch(() => null),
      fetch(`${base}/workstations`, { credentials: 'same-origin', headers }).then(r => r.json()).catch(() => null),
      fetch(`${base}/agents`, { credentials: 'same-origin', headers }).then(r => r.json()).catch(() => null),
      fetch(`${base}/evidence-witnesses`, { credentials: 'same-origin', headers }).then(r => r.json()).catch(() => null),
    ]).then(([devices, workstations, agents, witnesses]) => {
      setData({ devices, workstations, agents, witnesses });
      setLoading(false);
    });
  }, [selectedOrg, apiKey]);

  return (
    <div className="space-y-6">
      {/* Org selector */}
      {orgs.length > 1 && (
        <select
          value={selectedOrg}
          onChange={e => setSelectedOrg(e.target.value)}
          className="block w-full max-w-xs px-3 py-2 border border-slate-200 rounded-lg text-sm"
        >
          {orgs.map(o => <option key={o.id} value={o.id}>{o.name} ({o.site_count} sites)</option>)}
        </select>
      )}

      {loading ? (
        <div className="text-center py-12 text-slate-500">Loading inventory...</div>
      ) : (
        <>
          {/* KPI row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-white rounded-xl border border-slate-100 p-4 text-center">
              <p className="text-2xl font-bold text-slate-900">{data.devices?.summary?.total ?? 0}</p>
              <p className="text-xs text-slate-500">Devices ({data.devices?.summary?.compliance_rate ?? 0}% compliant)</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-100 p-4 text-center">
              <p className="text-2xl font-bold text-slate-900">{data.workstations?.summary?.total_workstations ?? 0}</p>
              <p className="text-xs text-slate-500">Workstations ({data.workstations?.summary?.overall_compliance_rate ?? 0}% compliant)</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-100 p-4 text-center">
              <p className="text-2xl font-bold text-slate-900">
                {data.agents?.summary?.active ?? 0}/{data.agents?.summary?.total ?? 0}
              </p>
              <p className="text-xs text-slate-500">Agents Active</p>
            </div>
            <div className="bg-white rounded-xl border border-slate-100 p-4 text-center">
              <p className="text-2xl font-bold text-emerald-600">{data.witnesses?.coverage_pct ?? 0}%</p>
              <p className="text-xs text-slate-500">Evidence Witnessed ({data.witnesses?.attestations_24h ?? 0} 24h)</p>
            </div>
          </div>

          {/* Device table */}
          {data.devices?.devices?.length > 0 && (
            <div className="bg-white rounded-xl border border-slate-100 overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-100">
                <h3 className="font-medium text-slate-900">Devices ({data.devices.devices.length})</h3>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-xs text-slate-500 uppercase">
                    <tr>
                      <th className="px-4 py-2 text-left">Hostname/IP</th>
                      <th className="px-4 py-2 text-left">Site</th>
                      <th className="px-4 py-2 text-left">Type</th>
                      <th className="px-4 py-2 text-left">OS</th>
                      <th className="px-4 py-2 text-left">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                    {data.devices.devices.slice(0, 50).map((d: any) => (
                      <tr key={d.id} className="hover:bg-slate-50">
                        <td className="px-4 py-2 font-mono text-xs">{d.hostname || d.ip_address}</td>
                        <td className="px-4 py-2">{d.clinic_name}</td>
                        <td className="px-4 py-2">{d.device_type}</td>
                        <td className="px-4 py-2 text-xs">{d.os_name || '--'}</td>
                        <td className="px-4 py-2">
                          <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                            d.compliance_status === 'compliant' ? 'bg-emerald-100 text-emerald-700' :
                            d.compliance_status === 'drifted' ? 'bg-red-100 text-red-700' :
                            'bg-slate-100 text-slate-600'
                          }`}>{d.compliance_status === 'drifted' ? 'non-compliant' : d.compliance_status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

// Error boundary to prevent dashboard crashes from propagating
class DashboardErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div className="p-8 text-center">
          <h2 className="text-lg font-semibold text-red-600 mb-2">Dashboard Error</h2>
          <p className="text-sm text-slate-500 mb-4">{this.state.error.message}</p>
          <button onClick={() => this.setState({ error: null })}
            className="px-4 py-2 text-sm bg-teal-600 text-white rounded hover:bg-teal-700">
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const PartnerDashboardWithBoundary: React.FC = () => (
  <DashboardErrorBoundary>
    <PartnerDashboard />
  </DashboardErrorBoundary>
);

export default PartnerDashboardWithBoundary;

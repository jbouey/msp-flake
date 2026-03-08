import React, { useState, useEffect, useCallback } from 'react';
import { GlassCard, Spinner, Badge } from '../components/shared';

const getToken = (): string | null => localStorage.getItem('auth_token');

interface Partner {
  id: string;
  name: string;
  slug: string;
  contact_email: string;
  brand_name: string;
  primary_color: string;
  logo_url: string | null;
  revenue_share_percent: number;
  status: 'active' | 'suspended' | 'inactive';
  site_count: number;
  created_at: string;
  pending_approval?: boolean;
  oauth_email?: string;
}

interface PendingPartner {
  id: string;
  name: string;
  slug: string;
  oauth_email: string;
  auth_provider: string;
  created_at: string;
}

interface PartnerStats {
  total: number;
  active: number;
  suspended: number;
  inactive: number;
  total_sites: number;
}

interface PartnerOAuthConfig {
  allowed_domains: string[];
  require_approval: boolean;
  allow_consumer_gmail: boolean;
  notify_emails: string[];
}

interface ActivityEvent {
  id: number;
  partner_id: string;
  partner_name: string | null;
  partner_slug: string | null;
  event_type: string;
  event_category: string;
  event_data: Record<string, unknown>;
  target_type: string | null;
  target_id: string | null;
  actor_ip: string | null;
  success: boolean;
  error_message: string | null;
  created_at: string;
}

interface ActivityStats {
  total: number;
  recent: number;
  auth_events: number;
  unique_partners: number;
}

interface PartnerDetail {
  id: string;
  name: string;
  slug: string;
  contact_email: string;
  contact_phone: string | null;
  brand_name: string;
  logo_url: string | null;
  primary_color: string;
  revenue_share_percent: number;
  status: string;
  created_at: string;
  updated_at: string | null;
  sites: Array<{
    site_id: string;
    clinic_name: string;
    status: string;
    tier: string;
    appliance_count: number;
    last_checkin: string | null;
  }>;
  users: Array<{
    id: string;
    email: string;
    name: string | null;
    role: string;
    status: string;
    last_login: string | null;
  }>;
  stats: {
    total_sites: number;
    total_users: number;
    total_appliances: number;
    total_incidents: number;
    open_incidents: number;
    total_activity: number;
    recent_activity: number;
  };
}

const EVENT_CATEGORY_COLORS: Record<string, string> = {
  auth: 'bg-blue-100 text-blue-800',
  admin: 'bg-purple-100 text-purple-800',
  site: 'bg-green-100 text-green-800',
  provision: 'bg-amber-100 text-amber-800',
  credential: 'bg-rose-100 text-rose-800',
  asset: 'bg-cyan-100 text-cyan-800',
  discovery: 'bg-indigo-100 text-indigo-800',
  learning: 'bg-teal-100 text-teal-800',
};

const getEventColor = (event: ActivityEvent): string =>
  EVENT_CATEGORY_COLORS[event.event_category] || 'bg-gray-100 text-gray-800';

const formatEventType = (type: string): string =>
  type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

const formatDateTime = (dateStr: string): string =>
  new Date(dateStr).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
  });
}

function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return 'Never';
  const diffMs = Date.now() - new Date(dateString).getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${Math.floor(diffHours / 24)}d ago`;
}

// ============================================================================
// Partner Row
// ============================================================================
const PartnerRow: React.FC<{ partner: Partner; onClick: () => void }> = ({ partner, onClick }) => (
  <tr onClick={onClick} className="hover:bg-fill-tertiary/50 cursor-pointer transition-colors">
    <td className="px-4 py-3">
      <div className="flex items-center gap-3">
        {partner.logo_url ? (
          <img src={partner.logo_url} alt={partner.brand_name} className="w-8 h-8 rounded" />
        ) : (
          <div className="w-8 h-8 rounded flex items-center justify-center text-white font-bold text-sm"
            style={{ backgroundColor: partner.primary_color || '#4F46E5' }}>
            {partner.brand_name.charAt(0)}
          </div>
        )}
        <div>
          <p className="font-medium text-label-primary">{partner.brand_name}</p>
          <p className="text-xs text-label-tertiary">{partner.slug}</p>
        </div>
      </div>
    </td>
    <td className="px-4 py-3 text-sm text-label-secondary">{partner.contact_email}</td>
    <td className="px-4 py-3">
      <Badge variant={partner.status === 'active' ? 'success' : partner.status === 'suspended' ? 'warning' : 'default'}>
        {partner.status}
      </Badge>
    </td>
    <td className="px-4 py-3 text-sm text-label-secondary">{partner.site_count}</td>
    <td className="px-4 py-3 text-sm text-label-secondary">{partner.revenue_share_percent}%</td>
    <td className="px-4 py-3 text-sm text-label-tertiary">{formatDate(partner.created_at)}</td>
  </tr>
);

// ============================================================================
// New Partner Modal
// ============================================================================
const NewPartnerModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { name: string; slug: string; contact_email: string; brand_name: string; primary_color: string; revenue_share_percent: number }) => void;
  isLoading: boolean;
}> = ({ isOpen, onClose, onSubmit, isLoading }) => {
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [brandName, setBrandName] = useState('');
  const [primaryColor, setPrimaryColor] = useState('#4F46E5');
  const [revenueShare, setRevenueShare] = useState(40);

  useEffect(() => {
    setSlug(name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''));
  }, [name]);

  useEffect(() => {
    if (!brandName && name) setBrandName(name);
  }, [name]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <GlassCard className="w-full max-w-lg">
        <h2 className="text-xl font-semibold mb-4">New Partner</h2>
        <form onSubmit={e => { e.preventDefault(); onSubmit({ name, slug, contact_email: contactEmail, brand_name: brandName, primary_color: primaryColor, revenue_share_percent: revenueShare }); }} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-label-secondary mb-1">Partner Name *</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="NEPA IT Solutions"
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none" required />
            </div>
            <div>
              <label className="block text-sm font-medium text-label-secondary mb-1">Slug</label>
              <input type="text" value={slug} onChange={e => setSlug(e.target.value)} placeholder="nepa-it"
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none" />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Contact Email *</label>
            <input type="email" value={contactEmail} onChange={e => setContactEmail(e.target.value)} placeholder="partner@example.com"
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none" required />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-label-secondary mb-1">Brand Name</label>
              <input type="text" value={brandName} onChange={e => setBrandName(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none" />
            </div>
            <div>
              <label className="block text-sm font-medium text-label-secondary mb-1">Brand Color</label>
              <div className="flex items-center gap-2">
                <input type="color" value={primaryColor} onChange={e => setPrimaryColor(e.target.value)} className="w-10 h-10 rounded cursor-pointer" />
                <input type="text" value={primaryColor} onChange={e => setPrimaryColor(e.target.value)}
                  className="flex-1 px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none" />
              </div>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Revenue Share (Partner %)</label>
            <div className="flex items-center gap-4">
              <input type="range" min="10" max="60" value={revenueShare} onChange={e => setRevenueShare(parseInt(e.target.value))} className="flex-1" />
              <span className="text-sm font-medium w-16">{revenueShare}% / {100 - revenueShare}%</span>
            </div>
          </div>
          <div className="flex gap-3 pt-2">
            <button type="button" onClick={onClose} className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors">Cancel</button>
            <button type="submit" disabled={!name || !contactEmail || isLoading}
              className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50">
              {isLoading ? 'Creating...' : 'Create Partner'}
            </button>
          </div>
        </form>
      </GlassCard>
    </div>
  );
};

// ============================================================================
// Partner Detail View (full in-page view with tabs)
// ============================================================================
const PartnerDetailView: React.FC<{
  partnerId: string;
  onBack: () => void;
  onRefreshList: () => void;
}> = ({ partnerId, onBack, onRefreshList }) => {
  const [detail, setDetail] = useState<PartnerDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'overview' | 'sites' | 'users' | 'activity'>('overview');
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  // Edit form state
  const [editName, setEditName] = useState('');
  const [editEmail, setEditEmail] = useState('');
  const [editPhone, setEditPhone] = useState('');
  const [editBrandName, setEditBrandName] = useState('');
  const [editColor, setEditColor] = useState('#4F46E5');
  const [editRevenueShare, setEditRevenueShare] = useState(40);
  const [editStatus, setEditStatus] = useState('active');

  // Activity state
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [activityLoading, setActivityLoading] = useState(false);

  const fetchDetail = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    setIsLoading(true);
    try {
      const res = await fetch(`/api/partners/${partnerId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setDetail(data);
        setEditName(data.name);
        setEditEmail(data.contact_email);
        setEditPhone(data.contact_phone || '');
        setEditBrandName(data.brand_name);
        setEditColor(data.primary_color || '#4F46E5');
        setEditRevenueShare(data.revenue_share_percent);
        setEditStatus(data.status);
      }
    } catch (error) {
      console.error('Failed to fetch partner detail:', error);
    } finally {
      setIsLoading(false);
    }
  }, [partnerId]);

  const fetchActivity = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    setActivityLoading(true);
    try {
      const res = await fetch(`/api/partners/${partnerId}/activity?limit=100`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setEvents(data.logs || []);
      }
    } catch {
      // ignore
    } finally {
      setActivityLoading(false);
    }
  }, [partnerId]);

  useEffect(() => { fetchDetail(); }, [fetchDetail]);
  useEffect(() => { if (activeTab === 'activity') fetchActivity(); }, [activeTab, fetchActivity]);

  const handleSave = async () => {
    const token = getToken();
    if (!token) return;
    setIsSaving(true);
    try {
      const res = await fetch(`/api/partners/${partnerId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          name: editName, contact_email: editEmail, contact_phone: editPhone || null,
          brand_name: editBrandName, primary_color: editColor,
          revenue_share_percent: editRevenueShare, status: editStatus,
        }),
      });
      if (res.ok) {
        setIsEditing(false);
        fetchDetail();
        onRefreshList();
      } else {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        alert(`Failed to update: ${err.detail}`);
      }
    } catch {
      alert('Failed to update partner');
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete partner "${detail?.brand_name}"? Sites will be unlinked but not deleted.`)) return;
    const token = getToken();
    if (!token) return;
    try {
      const res = await fetch(`/api/partners/${partnerId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        onRefreshList();
        onBack();
      } else {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        alert(`Failed to delete: ${err.detail}`);
      }
    } catch {
      alert('Failed to delete partner');
    }
  };

  const handleRegenKey = async () => {
    if (!confirm('Regenerate API key? The old key will stop working immediately.')) return;
    const token = getToken();
    if (!token) return;
    try {
      const res = await fetch(`/api/partners/${partnerId}/regenerate-key`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.api_key) {
          await navigator.clipboard.writeText(data.api_key).catch(() => {});
          alert(`New API Key (copied to clipboard):\n\n${data.api_key}\n\nSave this now.`);
        }
      } else {
        alert('Failed to regenerate API key');
      }
    } catch {
      alert('Failed to regenerate API key');
    }
  };

  if (isLoading || !detail) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner size="lg" />
      </div>
    );
  }

  const tabs = [
    { key: 'overview' as const, label: 'Overview' },
    { key: 'sites' as const, label: `Sites (${detail.stats.total_sites})` },
    { key: 'users' as const, label: `Users (${detail.stats.total_users})` },
    { key: 'activity' as const, label: 'Activity' },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start gap-4">
        <button onClick={onBack} className="p-2 mt-1 rounded-ios-sm hover:bg-fill-secondary transition-colors">
          <svg className="w-5 h-5 text-label-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div className="flex items-center gap-4 flex-1 min-w-0">
          <div className="w-14 h-14 rounded-xl flex items-center justify-center text-white font-bold text-2xl flex-shrink-0"
            style={{ backgroundColor: detail.primary_color || '#4F46E5' }}>
            {detail.brand_name.charAt(0)}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold text-label-primary truncate">{detail.brand_name}</h1>
              <Badge variant={detail.status === 'active' ? 'success' : detail.status === 'suspended' ? 'warning' : 'default'}>
                {detail.status}
              </Badge>
            </div>
            <p className="text-label-tertiary text-sm">{detail.slug} &middot; {detail.contact_email}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setIsEditing(!isEditing)}
            className="px-3 py-1.5 text-sm rounded-ios-sm bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors flex items-center gap-1.5">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
            {isEditing ? 'Cancel' : 'Edit'}
          </button>
          <button onClick={handleRegenKey}
            className="px-3 py-1.5 text-sm rounded-ios-sm bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors flex items-center gap-1.5">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
            </svg>
            API Key
          </button>
          <button onClick={handleDelete}
            className="px-3 py-1.5 text-sm rounded-ios-sm bg-health-critical/10 text-health-critical hover:bg-health-critical/20 transition-colors">
            Delete
          </button>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-accent-primary">{detail.stats.total_sites}</p>
          <p className="text-xs text-label-tertiary">Sites</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">{detail.stats.total_appliances}</p>
          <p className="text-xs text-label-tertiary">Appliances</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">{detail.stats.total_users}</p>
          <p className="text-xs text-label-tertiary">Users</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${detail.stats.open_incidents > 0 ? 'text-health-warning' : 'text-health-healthy'}`}>
            {detail.stats.open_incidents}
          </p>
          <p className="text-xs text-label-tertiary">Open Incidents</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">{detail.revenue_share_percent}%</p>
          <p className="text-xs text-label-tertiary">Revenue Share</p>
        </GlassCard>
      </div>

      {/* Edit form (collapsible) */}
      {isEditing && (
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4">Edit Partner</h2>
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs text-label-tertiary uppercase mb-1">Name</label>
                <input type="text" value={editName} onChange={e => setEditName(e.target.value)}
                  className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
              </div>
              <div>
                <label className="block text-xs text-label-tertiary uppercase mb-1">Brand Name</label>
                <input type="text" value={editBrandName} onChange={e => setEditBrandName(e.target.value)}
                  className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
              </div>
              <div>
                <label className="block text-xs text-label-tertiary uppercase mb-1">Status</label>
                <select value={editStatus} onChange={e => setEditStatus(e.target.value)}
                  className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                  <option value="active">Active</option>
                  <option value="suspended">Suspended</option>
                  <option value="inactive">Inactive</option>
                </select>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs text-label-tertiary uppercase mb-1">Contact Email</label>
                <input type="email" value={editEmail} onChange={e => setEditEmail(e.target.value)}
                  className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
              </div>
              <div>
                <label className="block text-xs text-label-tertiary uppercase mb-1">Contact Phone</label>
                <input type="text" value={editPhone} onChange={e => setEditPhone(e.target.value)}
                  className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
              </div>
              <div>
                <label className="block text-xs text-label-tertiary uppercase mb-1">Brand Color</label>
                <div className="flex items-center gap-2">
                  <input type="color" value={editColor} onChange={e => setEditColor(e.target.value)} className="w-8 h-8 rounded cursor-pointer" />
                  <input type="text" value={editColor} onChange={e => setEditColor(e.target.value)}
                    className="flex-1 px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
                </div>
              </div>
            </div>
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Revenue Share</label>
              <div className="flex items-center gap-4">
                <input type="range" min="10" max="60" value={editRevenueShare} onChange={e => setEditRevenueShare(parseInt(e.target.value))} className="flex-1" />
                <span className="text-sm font-medium w-28">Partner {editRevenueShare}% / Us {100 - editRevenueShare}%</span>
              </div>
            </div>
            <div className="flex justify-end gap-3">
              <button onClick={() => setIsEditing(false)} className="px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm">Cancel</button>
              <button onClick={handleSave} disabled={isSaving}
                className="px-6 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50 text-sm">
                {isSaving ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </div>
        </GlassCard>
      )}

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-separator-light">
        {tabs.map(tab => (
          <button key={tab.key} onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors relative ${
              activeTab === tab.key ? 'text-accent-primary' : 'text-label-tertiary hover:text-label-secondary'
            }`}>
            {tab.label}
            {activeTab === tab.key && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent-primary rounded-t" />}
          </button>
        ))}
      </div>

      {/* Overview tab */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <GlassCard>
            <h3 className="text-lg font-semibold mb-4">Partner Information</h3>
            <div className="space-y-3">
              {[
                ['Name', detail.name],
                ['Brand', detail.brand_name],
                ['Slug', detail.slug],
                ['Email', detail.contact_email],
                ['Phone', detail.contact_phone || '-'],
                ['Status', detail.status],
                ['Revenue Share', `${detail.revenue_share_percent}%`],
                ['Created', formatDate(detail.created_at)],
                ['Last Updated', detail.updated_at ? formatDate(detail.updated_at) : '-'],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between items-center py-1.5 border-b border-separator-light last:border-0">
                  <span className="text-sm text-label-tertiary">{label}</span>
                  <span className="text-sm text-label-primary font-medium">{value}</span>
                </div>
              ))}
            </div>
          </GlassCard>
          <GlassCard>
            <h3 className="text-lg font-semibold mb-4">Partner Dashboard Access</h3>
            <div className="space-y-4">
              <div>
                <p className="text-xs text-label-tertiary uppercase mb-1">Portal URL</p>
                <code className="block px-3 py-2 bg-fill-secondary rounded text-sm">
                  https://dashboard.osiriscare.net/partner/login
                </code>
              </div>
              <div>
                <p className="text-xs text-label-tertiary uppercase mb-1">API Authentication</p>
                <p className="text-sm text-label-secondary">
                  Partners authenticate via OAuth (Google/Microsoft), email/password, or API key in the <code className="bg-fill-secondary px-1 rounded">X-API-Key</code> header.
                </p>
              </div>
              <div className="pt-2 border-t border-separator-light">
                <p className="text-xs text-label-tertiary">
                  Total incidents across all sites: <strong>{detail.stats.total_incidents}</strong> ({detail.stats.open_incidents} open)
                </p>
              </div>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Sites tab */}
      {activeTab === 'sites' && (
        <GlassCard padding="none">
          {detail.sites.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-label-tertiary">No sites assigned to this partner.</p>
            </div>
          ) : (
            <table className="w-full">
              <thead className="bg-fill-secondary border-b border-separator-light">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Site</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Tier</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Appliances</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Last Checkin</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator-light">
                {detail.sites.map(site => (
                  <tr key={site.site_id} className="hover:bg-fill-tertiary/50 transition-colors">
                    <td className="px-4 py-3">
                      <p className="font-medium text-label-primary">{site.clinic_name}</p>
                      <p className="text-xs text-label-tertiary">{site.site_id}</p>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={site.tier === 'large' ? 'success' : site.tier === 'mid' ? 'info' : 'default'}>{site.tier}</Badge>
                    </td>
                    <td className="px-4 py-3 text-sm text-label-secondary">{site.appliance_count}</td>
                    <td className="px-4 py-3 text-sm text-label-tertiary">{formatRelativeTime(site.last_checkin)}</td>
                    <td className="px-4 py-3">
                      <Badge variant={site.status === 'active' ? 'success' : 'default'}>{site.status || 'active'}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </GlassCard>
      )}

      {/* Users tab */}
      {activeTab === 'users' && (
        <GlassCard padding="none">
          {detail.users.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-label-tertiary">No portal users for this partner.</p>
              <p className="text-label-tertiary text-sm mt-1">Users are created when partners sign up via OAuth or email.</p>
            </div>
          ) : (
            <table className="w-full">
              <thead className="bg-fill-secondary border-b border-separator-light">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Email</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Name</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Role</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Last Login</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator-light">
                {detail.users.map(user => (
                  <tr key={user.id} className="hover:bg-fill-tertiary/50 transition-colors">
                    <td className="px-4 py-3 text-sm text-label-primary font-medium">{user.email}</td>
                    <td className="px-4 py-3 text-sm text-label-secondary">{user.name || '-'}</td>
                    <td className="px-4 py-3">
                      <Badge variant={user.role === 'admin' ? 'info' : 'default'}>{user.role}</Badge>
                    </td>
                    <td className="px-4 py-3 text-sm text-label-tertiary">{user.last_login ? formatRelativeTime(user.last_login) : 'Never'}</td>
                    <td className="px-4 py-3">
                      <Badge variant={user.status === 'active' ? 'success' : 'default'}>{user.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </GlassCard>
      )}

      {/* Activity tab */}
      {activeTab === 'activity' && (
        <GlassCard padding="none">
          {activityLoading ? (
            <div className="flex items-center justify-center py-12"><Spinner size="lg" /></div>
          ) : events.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-label-tertiary">No activity recorded for this partner.</p>
            </div>
          ) : (
            <table className="w-full">
              <thead className="bg-fill-secondary border-b border-separator-light">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Time</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Event</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Target</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Details</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">IP</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator-light">
                {events.map(event => (
                  <tr key={event.id} className="hover:bg-fill-tertiary/50 transition-colors">
                    <td className="px-4 py-3">
                      <span className="text-sm text-label-secondary font-mono whitespace-nowrap">{formatDateTime(event.created_at)}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex px-2 py-1 text-xs font-medium rounded whitespace-nowrap ${getEventColor(event)}`}>
                        {formatEventType(event.event_type)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {event.target_type ? (
                        <span className="text-sm text-label-primary">{event.target_type}{event.target_id ? `: ${event.target_id.slice(0, 12)}` : ''}</span>
                      ) : (
                        <span className="text-sm text-label-tertiary">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-sm text-label-tertiary max-w-[200px] truncate block">
                        {event.error_message ? event.error_message
                          : Object.keys(event.event_data).length > 0
                            ? Object.entries(event.event_data).map(([k, v]) => `${k}: ${v}`).join(', ')
                            : '-'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs text-label-tertiary font-mono">{event.actor_ip || '-'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </GlassCard>
      )}
    </div>
  );
};

// ============================================================================
// Partner Activity Log (all partners)
// ============================================================================
const PartnerActivityLog: React.FC = () => {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [stats, setStats] = useState<ActivityStats>({ total: 0, recent: 0, auth_events: 0, unique_partners: 0 });
  const [isLoading, setIsLoading] = useState(true);
  const [filterCategory, setFilterCategory] = useState('all');
  const [filterPartner, setFilterPartner] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');

  const fetchActivity = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    setIsLoading(true);
    try {
      const params = new URLSearchParams({ limit: '200' });
      if (filterCategory !== 'all') params.set('event_category', filterCategory);
      if (filterPartner !== 'all') params.set('partner_id', filterPartner);
      const res = await fetch(`/api/partners/activity/all?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setEvents(data.logs || []);
        if (data.stats) setStats(data.stats);
      }
    } catch (error) {
      console.error('Failed to fetch partner activity:', error);
    } finally {
      setIsLoading(false);
    }
  }, [filterCategory, filterPartner]);

  useEffect(() => { fetchActivity(); }, [fetchActivity]);

  const partners = Array.from(
    new Map(events.filter(e => e.partner_name).map(e => [e.partner_id, e.partner_name!])).entries()
  );
  const categories = Array.from(new Set(events.map(e => e.event_category)));

  const filteredEvents = events.filter(e => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      (e.partner_name?.toLowerCase().includes(q)) ||
      e.event_type.toLowerCase().includes(q) ||
      (e.target_id?.toLowerCase().includes(q)) ||
      (e.actor_ip?.toLowerCase().includes(q)) ||
      JSON.stringify(e.event_data).toLowerCase().includes(q)
    );
  });

  const exportCsv = () => {
    const csv = [
      ['Timestamp', 'Partner', 'Event', 'Category', 'Target', 'IP', 'Success', 'Details'].join(','),
      ...filteredEvents.map(e => [
        e.created_at, `"${e.partner_name || e.partner_id}"`, e.event_type, e.event_category,
        `"${e.target_type ? `${e.target_type}:${e.target_id || ''}` : ''}"`,
        e.actor_ip || '', e.success ? 'yes' : 'no',
        `"${JSON.stringify(e.event_data).replace(/"/g, '""')}"`,
      ].join(','))
    ].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `partner-activity-${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Total Events</p>
          <p className="text-2xl font-semibold mt-1">{stats.total}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Last 24h</p>
          <p className="text-2xl font-semibold mt-1">{stats.recent}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Auth Events</p>
          <p className="text-2xl font-semibold text-health-healthy mt-1">{stats.auth_events}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Unique Partners</p>
          <p className="text-2xl font-semibold mt-1">{stats.unique_partners}</p>
        </GlassCard>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <div className="relative flex-1 max-w-md">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input type="text" placeholder="Search activity..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-white/50 border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary focus:border-transparent" />
        </div>
        <select value={filterCategory} onChange={e => setFilterCategory(e.target.value)}
          className="px-4 py-2 bg-white/50 border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary">
          <option value="all">All Categories</option>
          {categories.map(cat => <option key={cat} value={cat}>{cat.charAt(0).toUpperCase() + cat.slice(1)}</option>)}
        </select>
        <select value={filterPartner} onChange={e => setFilterPartner(e.target.value)}
          className="px-4 py-2 bg-white/50 border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary">
          <option value="all">All Partners</option>
          {partners.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select>
        <button onClick={exportCsv} className="btn-secondary flex items-center gap-2">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          Export CSV
        </button>
      </div>

      <GlassCard padding="none">
        {isLoading ? (
          <div className="flex items-center justify-center py-12"><Spinner size="lg" /></div>
        ) : filteredEvents.length === 0 ? (
          <div className="text-center py-12">
            <svg className="w-12 h-12 text-label-tertiary mx-auto mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p className="text-label-secondary">No partner activity found</p>
            <p className="text-label-tertiary text-sm mt-1">Activity will appear here as partners use the system</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-fill-secondary border-b border-separator-light">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Time</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Partner</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Event</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Target</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Details</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">IP</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator-light">
                {filteredEvents.map(event => (
                  <tr key={event.id} className="hover:bg-fill-tertiary/50 transition-colors">
                    <td className="px-4 py-3"><span className="text-sm text-label-secondary font-mono whitespace-nowrap">{formatDateTime(event.created_at)}</span></td>
                    <td className="px-4 py-3"><span className="text-sm font-medium text-label-primary">{event.partner_name || event.partner_slug || event.partner_id.slice(0, 8)}</span></td>
                    <td className="px-4 py-3"><span className={`inline-flex px-2 py-1 text-xs font-medium rounded whitespace-nowrap ${getEventColor(event)}`}>{formatEventType(event.event_type)}</span></td>
                    <td className="px-4 py-3">{event.target_type ? <span className="text-sm text-label-primary">{event.target_type}{event.target_id ? `: ${event.target_id.slice(0, 12)}` : ''}</span> : <span className="text-sm text-label-tertiary">-</span>}</td>
                    <td className="px-4 py-3"><span className="text-sm text-label-tertiary max-w-[200px] truncate block">{event.error_message ? event.error_message : Object.keys(event.event_data).length > 0 ? Object.entries(event.event_data).map(([k, v]) => `${k}: ${v}`).join(', ') : '-'}</span></td>
                    <td className="px-4 py-3"><span className="text-xs text-label-tertiary font-mono">{event.actor_ip || '-'}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      <GlassCard padding="sm">
        <p className="text-xs text-label-tertiary text-center">
          Partner activity is logged to an append-only audit table for HIPAA 164.312(b) compliance.
        </p>
      </GlassCard>
    </div>
  );
};

// ============================================================================
// Main Partners Page
// ============================================================================
export const Partners: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'partners' | 'activity'>('partners');
  const [partners, setPartners] = useState<Partner[]>([]);
  const [pendingPartners, setPendingPartners] = useState<PendingPartner[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showNewPartnerModal, setShowNewPartnerModal] = useState(false);
  const [selectedPartnerId, setSelectedPartnerId] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [processingApproval, setProcessingApproval] = useState<string | null>(null);

  // Pagination & search state
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [sortBy, setSortBy] = useState<string>('name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<PartnerStats>({ total: 0, active: 0, suspended: 0, inactive: 0, total_sites: 0 });
  const limit = 25;

  // OAuth config state
  const [oauthConfig, setOauthConfig] = useState<PartnerOAuthConfig | null>(null);
  const [showOAuthSettings, setShowOAuthSettings] = useState(false);
  const [oauthDomains, setOauthDomains] = useState('');
  const [oauthRequireApproval, setOauthRequireApproval] = useState(true);
  const [savingOAuth, setSavingOAuth] = useState(false);

  // Debounce search input
  useEffect(() => {
    if (search === debouncedSearch) return;
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search, debouncedSearch]);

  const fetchPartners = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    setIsLoading(true);
    try {
      const params = new URLSearchParams({
        sort_by: sortBy,
        sort_dir: sortDir,
        limit: String(limit),
        offset: String(page * limit),
      });
      if (statusFilter) params.set('status', statusFilter);
      if (debouncedSearch) params.set('search', debouncedSearch);
      const response = await fetch(`/api/partners?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        const data = await response.json();
        setPartners(data.partners || []);
        setTotal(data.total || 0);
        if (data.stats) setStats(data.stats);
      }
    } catch (error) {
      console.error('Failed to fetch partners:', error);
    } finally {
      setIsLoading(false);
    }
  }, [debouncedSearch, statusFilter, sortBy, sortDir, page]);

  useEffect(() => { fetchPartners(); }, [fetchPartners]);

  useEffect(() => {
    fetchPendingPartners();
    fetchOAuthConfig();
  }, []);

  const fetchPendingPartners = async () => {
    const token = getToken();
    if (!token) return;
    try {
      const response = await fetch('/api/admin/partners/pending', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        const data = await response.json();
        setPendingPartners(data.pending || []);
      }
    } catch (error) {
      console.error('Failed to fetch pending partners:', error);
    }
  };

  const fetchOAuthConfig = async () => {
    const token = getToken();
    if (!token) return;
    try {
      const response = await fetch('/api/admin/partners/oauth-config', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        const data = await response.json();
        setOauthConfig(data);
        setOauthDomains(data.allowed_domains?.join(', ') || '');
        setOauthRequireApproval(data.require_approval ?? true);
      }
    } catch (error) {
      console.error('Failed to fetch OAuth config:', error);
    }
  };

  const saveOAuthConfig = async () => {
    const token = getToken();
    if (!token) return;
    setSavingOAuth(true);
    try {
      const domains = oauthDomains.split(',').map(d => d.trim().toLowerCase()).filter(d => d);
      const response = await fetch('/api/admin/partners/oauth-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          allowed_domains: domains, require_approval: oauthRequireApproval,
          allow_consumer_gmail: oauthConfig?.allow_consumer_gmail ?? true,
          notify_emails: oauthConfig?.notify_emails ?? [],
        }),
      });
      if (response.ok) {
        setShowOAuthSettings(false);
        fetchOAuthConfig();
      } else {
        const error = await response.json();
        alert(`Failed to save OAuth settings: ${error.detail || 'Unknown error'}`);
      }
    } catch {
      alert('Failed to save OAuth settings');
    } finally {
      setSavingOAuth(false);
    }
  };

  const handleApprovePartner = async (partnerId: string) => {
    const token = getToken();
    if (!token) return;
    setProcessingApproval(partnerId);
    try {
      const response = await fetch(`/api/admin/partners/approve/${partnerId}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        fetchPendingPartners();
        fetchPartners();
      } else {
        const error = await response.json();
        alert(`Failed to approve partner: ${error.detail || 'Unknown error'}`);
      }
    } catch {
      alert('Failed to approve partner');
    } finally {
      setProcessingApproval(null);
    }
  };

  const handleRejectPartner = async (partnerId: string) => {
    if (!confirm('Are you sure you want to reject this partner signup? This will delete their account.')) return;
    const token = getToken();
    if (!token) return;
    setProcessingApproval(partnerId);
    try {
      const response = await fetch(`/api/admin/partners/reject/${partnerId}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        fetchPendingPartners();
      } else {
        const error = await response.json();
        alert(`Failed to reject partner: ${error.detail || 'Unknown error'}`);
      }
    } catch {
      alert('Failed to reject partner');
    } finally {
      setProcessingApproval(null);
    }
  };

  const handleCreatePartner = async (data: {
    name: string; slug: string; contact_email: string;
    brand_name: string; primary_color: string; revenue_share_percent: number;
  }) => {
    setIsCreating(true);
    try {
      const token = getToken();
      const response = await fetch('/api/partners', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(data),
      });
      if (response.ok) {
        const result = await response.json();
        setShowNewPartnerModal(false);
        fetchPartners();
        if (result.api_key) {
          alert(`Partner created!\n\nAPI Key (copy now, won't be shown again):\n${result.api_key}`);
        }
      } else {
        const error = await response.json();
        alert(`Failed to create partner: ${error.detail || 'Unknown error'}`);
      }
    } catch {
      alert('Failed to create partner');
    } finally {
      setIsCreating(false);
    }
  };

  const totalPages = Math.ceil(total / limit);

  const handleSort = (col: string) => {
    if (sortBy === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(col);
      setSortDir('asc');
    }
    setPage(0);
  };

  const SortIcon: React.FC<{ col: string }> = ({ col }) => {
    if (sortBy !== col) return <span className="text-label-quaternary ml-1">↕</span>;
    return <span className="text-accent-primary ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  };

  // If a partner is selected, show the detail view
  if (selectedPartnerId) {
    return (
      <PartnerDetailView
        partnerId={selectedPartnerId}
        onBack={() => setSelectedPartnerId(null)}
        onRefreshList={fetchPartners}
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary">Partners</h1>
          <p className="text-label-tertiary text-sm mt-1">Manage MSP partners and resellers</p>
        </div>
        {activeTab === 'partners' && (
          <button onClick={() => setShowNewPartnerModal(true)} className="btn-primary">+ New Partner</button>
        )}
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-separator-light">
        {[
          { key: 'partners' as const, label: 'Partners' },
          { key: 'activity' as const, label: 'Activity Log' },
        ].map(tab => (
          <button key={tab.key} onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors relative ${
              activeTab === tab.key ? 'text-accent-primary' : 'text-label-tertiary hover:text-label-secondary'
            }`}>
            {tab.label}
            {activeTab === tab.key && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent-primary rounded-t" />}
          </button>
        ))}
      </div>

      {/* Activity Log tab */}
      {activeTab === 'activity' && <PartnerActivityLog />}

      {/* Partners tab content */}
      {activeTab === 'partners' && <>
        {/* Stats row */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <GlassCard padding="md" className="text-center">
            <p className="text-2xl font-bold text-accent-primary">{stats.total}</p>
            <p className="text-xs text-label-tertiary">Total Partners</p>
          </GlassCard>
          <GlassCard padding="md" className="text-center">
            <p className="text-2xl font-bold text-health-healthy">{stats.active}</p>
            <p className="text-xs text-label-tertiary">Active</p>
          </GlassCard>
          <GlassCard padding="md" className="text-center">
            <p className="text-2xl font-bold text-health-warning">{stats.suspended}</p>
            <p className="text-xs text-label-tertiary">Suspended</p>
          </GlassCard>
          <GlassCard padding="md" className="text-center">
            <p className="text-2xl font-bold text-label-primary">{stats.total_sites}</p>
            <p className="text-xs text-label-tertiary">Total Sites</p>
          </GlassCard>
          <GlassCard padding="md" className="text-center">
            <p className="text-2xl font-bold text-health-warning">{pendingPartners.length}</p>
            <p className="text-xs text-label-tertiary">Pending Approval</p>
          </GlassCard>
        </div>

        {/* Pending Approvals */}
        {pendingPartners.length > 0 && (
          <GlassCard>
            <div className="flex items-center gap-2 mb-4">
              <div className="w-2 h-2 rounded-full bg-health-warning animate-pulse" />
              <h2 className="text-lg font-semibold text-label-primary">Pending Partner Approvals</h2>
              <Badge variant="warning">{pendingPartners.length}</Badge>
            </div>
            <div className="space-y-3">
              {pendingPartners.map(pending => (
                <div key={pending.id} className="flex items-center justify-between p-4 bg-fill-secondary rounded-lg border border-separator-light">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-full bg-health-warning/20 flex items-center justify-center">
                      {pending.auth_provider === 'google' ? (
                        <svg className="w-5 h-5" viewBox="0 0 24 24">
                          <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                          <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                          <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                          <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                        </svg>
                      ) : (
                        <svg className="w-5 h-5" viewBox="0 0 21 21">
                          <rect fill="#f25022" x="1" y="1" width="9" height="9"/>
                          <rect fill="#7fba00" x="11" y="1" width="9" height="9"/>
                          <rect fill="#05a6f0" x="1" y="11" width="9" height="9"/>
                          <rect fill="#ffba08" x="11" y="11" width="9" height="9"/>
                        </svg>
                      )}
                    </div>
                    <div>
                      <p className="font-medium text-label-primary">{pending.name}</p>
                      <p className="text-sm text-label-secondary">{pending.oauth_email}</p>
                      <p className="text-xs text-label-tertiary">
                        Signed up via {pending.auth_provider === 'google' ? 'Google' : 'Microsoft'} on {formatDate(pending.created_at)}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => handleRejectPartner(pending.id)} disabled={processingApproval === pending.id}
                      className="px-3 py-1.5 text-sm rounded-ios bg-health-critical/10 text-health-critical hover:bg-health-critical/20 transition-colors disabled:opacity-50">
                      Reject
                    </button>
                    <button onClick={() => handleApprovePartner(pending.id)} disabled={processingApproval === pending.id}
                      className="px-3 py-1.5 text-sm rounded-ios bg-health-healthy text-white hover:bg-health-healthy/90 transition-colors disabled:opacity-50">
                      {processingApproval === pending.id ? 'Processing...' : 'Approve'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </GlassCard>
        )}

        {/* OAuth Settings */}
        <GlassCard>
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <svg className="w-5 h-5 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
              <h2 className="text-lg font-semibold text-label-primary">Partner OAuth Settings</h2>
            </div>
            <button onClick={() => setShowOAuthSettings(!showOAuthSettings)} className="text-sm text-accent-primary hover:underline">
              {showOAuthSettings ? 'Hide' : 'Configure'}
            </button>
          </div>
          {!showOAuthSettings && oauthConfig && (
            <div className="flex flex-wrap gap-3 text-sm">
              <span className="px-2 py-1 rounded bg-fill-secondary text-label-secondary">
                {oauthConfig.require_approval ? 'Approval Required' : 'Auto-approve'}
              </span>
              {oauthConfig.allowed_domains.length > 0 ? (
                <span className="px-2 py-1 rounded bg-health-healthy/20 text-health-healthy">
                  {oauthConfig.allowed_domains.length} domain{oauthConfig.allowed_domains.length !== 1 ? 's' : ''} whitelisted
                </span>
              ) : (
                <span className="px-2 py-1 rounded bg-health-warning/20 text-health-warning">No domains whitelisted</span>
              )}
            </div>
          )}
          {showOAuthSettings && (
            <div className="space-y-4 pt-2">
              <div>
                <label className="block text-sm font-medium text-label-primary mb-1">Whitelisted Domains (Auto-Approve)</label>
                <input type="text" value={oauthDomains} onChange={e => setOauthDomains(e.target.value)} placeholder="company.com, partner.net"
                  className="w-full px-3 py-2 border border-separator-light rounded-ios focus:ring-2 focus:ring-accent-primary focus:border-transparent bg-fill-primary" />
                <p className="text-xs text-label-tertiary mt-1">Comma-separated.</p>
              </div>
              <div className="flex items-center gap-2">
                <input type="checkbox" id="requireApproval" checked={oauthRequireApproval} onChange={e => setOauthRequireApproval(e.target.checked)} className="rounded" />
                <label htmlFor="requireApproval" className="text-sm text-label-primary">
                  Require admin approval for new partners (except whitelisted domains)
                </label>
              </div>
              {oauthConfig?.allowed_domains && oauthConfig.allowed_domains.length > 0 && (
                <div className="p-3 bg-fill-secondary rounded-ios">
                  <p className="text-xs text-label-secondary mb-2">Currently whitelisted:</p>
                  <div className="flex flex-wrap gap-2">
                    {oauthConfig.allowed_domains.map(domain => (
                      <span key={domain} className="px-2 py-1 bg-health-healthy/20 text-health-healthy text-xs rounded">{domain}</span>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <button onClick={() => setShowOAuthSettings(false)} className="px-4 py-2 text-sm text-label-secondary hover:bg-fill-tertiary rounded-ios">Cancel</button>
                <button onClick={saveOAuthConfig} disabled={savingOAuth}
                  className="px-4 py-2 text-sm bg-accent-primary text-white rounded-ios hover:bg-accent-primary/90 disabled:opacity-50">
                  {savingOAuth ? 'Saving...' : 'Save Settings'}
                </button>
              </div>
            </div>
          )}
        </GlassCard>

        {/* Search + Filter bar */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
          <div className="relative flex-1 max-w-md">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search partners..."
              className="w-full pl-10 pr-3 py-2 text-sm border border-separator-light rounded-ios bg-fill-primary focus:ring-2 focus:ring-accent-primary focus:border-transparent"
            />
          </div>
          <div className="flex gap-1">
            {[
              { value: undefined, label: 'All' },
              { value: 'active', label: 'Active' },
              { value: 'suspended', label: 'Suspended' },
              { value: 'inactive', label: 'Inactive' },
            ].map(option => (
              <button key={option.value || 'all'} onClick={() => { setStatusFilter(option.value); setPage(0); }}
                className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                  statusFilter === option.value ? 'bg-accent-primary text-white' : 'bg-separator-light text-label-secondary hover:bg-separator-light/80'
                }`}>
                {option.label}
              </button>
            ))}
          </div>
        </div>

        {/* Partners table */}
        <GlassCard padding="none">
          {isLoading ? (
            <div className="flex items-center justify-center py-12"><Spinner size="lg" /></div>
          ) : partners.length === 0 ? (
            <div className="text-center py-12">
              <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-accent-primary/10 flex items-center justify-center">
                <svg className="w-8 h-8 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                </svg>
              </div>
              <h3 className="font-semibold text-label-primary mb-2">
                {debouncedSearch ? 'No partners match your search' : 'No partners yet'}
              </h3>
              <p className="text-label-tertiary text-sm mb-4">
                {debouncedSearch ? 'Try a different search term.' : 'Click "New Partner" to add your first reseller.'}
              </p>
              {!debouncedSearch && <button onClick={() => setShowNewPartnerModal(true)} className="btn-primary">+ New Partner</button>}
            </div>
          ) : (
            <>
              <table className="w-full">
                <thead className="bg-fill-secondary border-b border-separator-light">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider cursor-pointer select-none hover:text-label-primary" onClick={() => handleSort('name')}>
                      Partner<SortIcon col="name" />
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">Contact</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider cursor-pointer select-none hover:text-label-primary" onClick={() => handleSort('status')}>
                      Status<SortIcon col="status" />
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider cursor-pointer select-none hover:text-label-primary" onClick={() => handleSort('site_count')}>
                      Sites<SortIcon col="site_count" />
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider cursor-pointer select-none hover:text-label-primary" onClick={() => handleSort('revenue_share_percent')}>
                      Revenue %<SortIcon col="revenue_share_percent" />
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider cursor-pointer select-none hover:text-label-primary" onClick={() => handleSort('created_at')}>
                      Created<SortIcon col="created_at" />
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-separator-light">
                  {partners.map(partner => (
                    <PartnerRow key={partner.id} partner={partner} onClick={() => setSelectedPartnerId(partner.id)} />
                  ))}
                </tbody>
              </table>
              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between px-4 py-3 border-t border-separator-light bg-fill-secondary">
                  <p className="text-sm text-label-tertiary">
                    Showing {page * limit + 1}–{Math.min((page + 1) * limit, total)} of {total}
                  </p>
                  <div className="flex items-center gap-1">
                    <button onClick={() => setPage(0)} disabled={page === 0}
                      className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">
                      ««
                    </button>
                    <button onClick={() => setPage(p => p - 1)} disabled={page === 0}
                      className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">
                      «
                    </button>
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
                      className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">
                      »
                    </button>
                    <button onClick={() => setPage(totalPages - 1)} disabled={page >= totalPages - 1}
                      className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">
                      »»
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </GlassCard>
      </>}

      {/* New Partner Modal */}
      <NewPartnerModal
        isOpen={showNewPartnerModal}
        onClose={() => setShowNewPartnerModal(false)}
        onSubmit={handleCreatePartner}
        isLoading={isCreating}
      />
    </div>
  );
};

export default Partners;

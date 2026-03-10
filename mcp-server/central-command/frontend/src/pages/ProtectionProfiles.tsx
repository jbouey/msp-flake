import React, { useState, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { GlassCard, Spinner } from '../components/shared';
import { protectionProfilesApi } from '../utils/api';
import type { ProtectionProfileSummary, ProfileAsset } from '../utils/api';

const STATUS_COLORS: Record<string, string> = {
  draft: 'text-label-tertiary',
  discovering: 'text-ios-blue',
  discovered: 'text-ios-purple',
  baseline_locked: 'text-health-warning',
  active: 'text-health-healthy',
  paused: 'text-label-tertiary',
  archived: 'text-label-quaternary',
};

const STATUS_BG: Record<string, string> = {
  draft: 'bg-fill-tertiary',
  discovering: 'bg-ios-blue/10',
  discovered: 'bg-ios-purple/10',
  baseline_locked: 'bg-health-warning/10',
  active: 'bg-health-healthy/10',
  paused: 'bg-fill-tertiary',
  archived: 'bg-fill-quaternary',
};

const ASSET_TYPE_LABELS: Record<string, string> = {
  service: 'Services',
  port: 'Ports',
  registry_key: 'Registry Keys',
  scheduled_task: 'Scheduled Tasks',
  config_file: 'Config Files',
  database_conn: 'Database Connections',
  iis_binding: 'IIS Bindings',
  odbc_dsn: 'ODBC DSNs',
  process: 'Processes',
};

const ASSET_TYPE_ICONS: Record<string, string> = {
  service: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
  port: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1',
  registry_key: 'M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z',
  scheduled_task: 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z',
  config_file: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
  process: 'M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2z',
};

// ─── List View ──────────────────────────────────────────────────────────────

export const ProtectionProfiles: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);

  const { data: profiles, isLoading } = useQuery({
    queryKey: ['protection-profiles', siteId],
    queryFn: () => protectionProfilesApi.list(siteId!),
    enabled: !!siteId,
  });

  const { data: templates } = useQuery({
    queryKey: ['protection-profile-templates'],
    queryFn: () => protectionProfilesApi.listTemplates(),
  });

  const createMutation = useMutation({
    mutationFn: (data: { site_id: string; name: string; description?: string; template_id?: string }) =>
      selectedTemplate
        ? protectionProfilesApi.createFromTemplate(data.site_id, selectedTemplate, data.name)
        : protectionProfilesApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['protection-profiles'] });
      setShowCreate(false);
      setNewName('');
      setNewDesc('');
      setSelectedTemplate(null);
    },
  });

  const handleCreate = () => {
    if (!siteId || !newName.trim()) return;
    createMutation.mutate({
      site_id: siteId,
      name: newName.trim(),
      description: newDesc.trim() || undefined,
      template_id: selectedTemplate || undefined,
    });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-5 page-enter">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <button
            onClick={() => navigate(`/sites/${siteId}`)}
            className="text-xs text-accent-primary font-medium hover:underline mb-1 block"
          >
            &larr; Back to Site
          </button>
          <h1 className="text-xl font-bold text-label-primary">App Protection Profiles</h1>
          <p className="text-sm text-label-secondary mt-0.5">
            Protect proprietary applications with automated baseline enforcement
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="btn-primary text-sm px-4 py-2"
        >
          + New Profile
        </button>
      </div>

      {/* Create Modal */}
      {showCreate && (
        <GlassCard>
          <h2 className="text-base font-semibold mb-3 text-label-primary">Create Protection Profile</h2>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-label-secondary font-medium block mb-1">Application Name</label>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Epic EHR, Dentrix"
                className="w-full px-3 py-2 text-sm bg-fill-secondary rounded-ios border border-separator-light text-label-primary placeholder:text-label-quaternary"
              />
            </div>
            <div>
              <label className="text-xs text-label-secondary font-medium block mb-1">Description (optional)</label>
              <input
                type="text"
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="Brief description of the application"
                className="w-full px-3 py-2 text-sm bg-fill-secondary rounded-ios border border-separator-light text-label-primary placeholder:text-label-quaternary"
              />
            </div>

            {/* Template Selection */}
            {templates && templates.length > 0 && (
              <div>
                <label className="text-xs text-label-secondary font-medium block mb-1">Start from Template (optional)</label>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                  {templates.map((t) => (
                    <button
                      key={t.id}
                      onClick={() => {
                        setSelectedTemplate(selectedTemplate === t.id ? null : t.id);
                        if (!newName) setNewName(t.name);
                      }}
                      className={`px-3 py-2 text-left text-sm rounded-ios border transition-all ${
                        selectedTemplate === t.id
                          ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                          : 'border-separator-light bg-fill-secondary text-label-primary hover:border-accent-primary/50'
                      }`}
                    >
                      <div className="font-medium">{t.name}</div>
                      <div className="text-xs text-label-tertiary">{t.category}</div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="flex gap-2 pt-1">
              <button
                onClick={handleCreate}
                disabled={!newName.trim() || createMutation.isPending}
                className="btn-primary text-sm px-4 py-1.5 disabled:opacity-50"
              >
                {createMutation.isPending ? 'Creating...' : 'Create'}
              </button>
              <button
                onClick={() => { setShowCreate(false); setNewName(''); setNewDesc(''); setSelectedTemplate(null); }}
                className="btn-secondary text-sm px-4 py-1.5"
              >
                Cancel
              </button>
            </div>
          </div>
        </GlassCard>
      )}

      {/* Profile Cards */}
      {profiles && profiles.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {profiles.map((profile) => (
            <ProfileCard
              key={profile.id}
              profile={profile}
              onClick={() => navigate(`/sites/${siteId}/protection/${profile.id}`)}
            />
          ))}
        </div>
      ) : (
        <GlassCard>
          <div className="text-center py-12">
            <svg className="w-12 h-12 mx-auto text-label-quaternary mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
            </svg>
            <h3 className="text-base font-semibold text-label-primary mb-1">No Protection Profiles</h3>
            <p className="text-sm text-label-secondary mb-4">
              Create a profile to protect a proprietary business application
            </p>
            <button onClick={() => setShowCreate(true)} className="btn-primary text-sm px-4 py-2">
              + Create First Profile
            </button>
          </div>
        </GlassCard>
      )}
    </div>
  );
};

// ─── Profile Card ───────────────────────────────────────────────────────────

const ProfileCard: React.FC<{ profile: ProtectionProfileSummary; onClick: () => void }> = ({ profile, onClick }) => (
  <GlassCard padding="md">
    <button onClick={onClick} className="w-full text-left">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-label-primary truncate">{profile.name}</h3>
        <span className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full ${STATUS_BG[profile.status] || ''} ${STATUS_COLORS[profile.status] || ''}`}>
          {profile.status.replace('_', ' ')}
        </span>
      </div>
      {profile.description && (
        <p className="text-xs text-label-secondary mb-3 line-clamp-2">{profile.description}</p>
      )}
      <div className="flex items-center gap-4 text-xs text-label-tertiary">
        <span className="tabular-nums">{profile.enabled_asset_count}/{profile.asset_count} assets</span>
        <span className="tabular-nums">{profile.rule_count} rules</span>
      </div>
    </button>
  </GlassCard>
);

// ─── Detail View ────────────────────────────────────────────────────────────

export const ProtectionProfileView: React.FC = () => {
  const { siteId, profileId } = useParams<{ siteId: string; profileId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const { data: profile, isLoading } = useQuery({
    queryKey: ['protection-profile', profileId],
    queryFn: () => protectionProfilesApi.get(profileId!, siteId!),
    enabled: !!profileId && !!siteId,
    refetchInterval: 10_000, // Poll during discovery
  });

  const discoverMutation = useMutation({
    mutationFn: () => protectionProfilesApi.discover(profileId!, siteId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['protection-profile', profileId] });
      setFeedback({ type: 'success', message: 'Discovery scan triggered. Results will appear shortly.' });
    },
    onError: (e: Error) => setFeedback({ type: 'error', message: e.message }),
  });

  const lockMutation = useMutation({
    mutationFn: () => protectionProfilesApi.lockBaseline(profileId!, siteId!),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['protection-profile', profileId] });
      queryClient.invalidateQueries({ queryKey: ['protection-profiles'] });
      setFeedback({ type: 'success', message: `Baseline locked: ${data.assets_protected} assets, ${data.rules_created} L1 rules created` });
    },
    onError: (e: Error) => setFeedback({ type: 'error', message: e.message }),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ assetId, enabled }: { assetId: string; enabled: boolean }) =>
      protectionProfilesApi.toggleAsset(profileId!, assetId, siteId!, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['protection-profile', profileId] }),
  });

  const pauseMutation = useMutation({
    mutationFn: () => protectionProfilesApi.pause(profileId!, siteId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['protection-profile', profileId] });
      setFeedback({ type: 'success', message: 'Protection paused' });
    },
  });

  const resumeMutation = useMutation({
    mutationFn: () => protectionProfilesApi.resume(profileId!, siteId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['protection-profile', profileId] });
      setFeedback({ type: 'success', message: 'Protection resumed' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => protectionProfilesApi.delete(profileId!, siteId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['protection-profiles'] });
      navigate(`/sites/${siteId}/protection`);
    },
  });

  // Group assets by type
  const assetsByType = useMemo(() => {
    if (!profile?.assets) return {};
    const grouped: Record<string, ProfileAsset[]> = {};
    for (const asset of profile.assets) {
      if (!grouped[asset.asset_type]) grouped[asset.asset_type] = [];
      grouped[asset.asset_type].push(asset);
    }
    return grouped;
  }, [profile?.assets]);

  if (isLoading) return <div className="flex items-center justify-center h-64"><Spinner /></div>;
  if (!profile) return <div className="text-center py-12 text-label-secondary">Profile not found</div>;

  const canDiscover = profile.status === 'draft' || profile.status === 'discovered';
  const canLock = profile.status === 'discovered' && profile.assets.some(a => a.enabled);
  const canPause = profile.status === 'active';
  const canResume = profile.status === 'paused';

  return (
    <div className="space-y-5 page-enter">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <button
            onClick={() => navigate(`/sites/${siteId}/protection`)}
            className="text-xs text-accent-primary font-medium hover:underline mb-1 block"
          >
            &larr; All Profiles
          </button>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-label-primary">{profile.name}</h1>
            <span className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full ${STATUS_BG[profile.status] || ''} ${STATUS_COLORS[profile.status] || ''}`}>
              {profile.status.replace('_', ' ')}
            </span>
          </div>
          {profile.description && (
            <p className="text-sm text-label-secondary mt-0.5">{profile.description}</p>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex gap-2">
          {canDiscover && (
            <button
              onClick={() => discoverMutation.mutate()}
              disabled={discoverMutation.isPending}
              className="btn-primary text-sm px-4 py-2 disabled:opacity-50"
            >
              {discoverMutation.isPending ? 'Scanning...' : profile.status === 'discovered' ? 'Re-Discover' : 'Run Discovery'}
            </button>
          )}
          {canLock && (
            <button
              onClick={() => lockMutation.mutate()}
              disabled={lockMutation.isPending}
              className="text-sm px-4 py-2 rounded-ios font-medium bg-health-healthy/10 text-health-healthy hover:bg-health-healthy/20 disabled:opacity-50"
            >
              {lockMutation.isPending ? 'Locking...' : 'Lock Baseline & Activate'}
            </button>
          )}
          {canPause && (
            <button
              onClick={() => pauseMutation.mutate()}
              className="btn-secondary text-sm px-4 py-2"
            >
              Pause
            </button>
          )}
          {canResume && (
            <button
              onClick={() => resumeMutation.mutate()}
              className="btn-primary text-sm px-4 py-2"
            >
              Resume
            </button>
          )}
          <button
            onClick={() => { if (confirm('Archive this profile? L1 rules will be disabled.')) deleteMutation.mutate(); }}
            className="text-sm px-3 py-2 rounded-ios text-health-critical hover:bg-health-critical/10"
          >
            Archive
          </button>
        </div>
      </div>

      {/* Feedback toast */}
      {feedback && (
        <div className={`px-4 py-2 rounded-ios text-sm ${
          feedback.type === 'success' ? 'bg-health-healthy/10 text-health-healthy' : 'bg-health-critical/10 text-health-critical'
        }`}>
          {feedback.message}
          <button onClick={() => setFeedback(null)} className="ml-2 opacity-60 hover:opacity-100">&times;</button>
        </div>
      )}

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <GlassCard padding="md">
          <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Assets</p>
          <p className="text-2xl font-bold mt-1 tabular-nums text-label-primary">
            {profile.enabled_asset_count}<span className="text-sm text-label-tertiary">/{profile.asset_count}</span>
          </p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">L1 Rules</p>
          <p className="text-2xl font-bold mt-1 tabular-nums text-ios-blue">{profile.rule_count}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Asset Types</p>
          <p className="text-2xl font-bold mt-1 tabular-nums text-ios-purple">{Object.keys(assetsByType).length}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Status</p>
          <p className={`text-lg font-bold mt-1 capitalize ${STATUS_COLORS[profile.status] || ''}`}>
            {profile.status.replace('_', ' ')}
          </p>
        </GlassCard>
      </div>

      {/* Discovering spinner */}
      {profile.status === 'discovering' && (
        <GlassCard>
          <div className="flex items-center gap-3 py-4">
            <Spinner />
            <div>
              <p className="text-sm font-semibold text-label-primary">Discovery in progress...</p>
              <p className="text-xs text-label-secondary">Scanning Windows targets for application assets. This page will auto-refresh.</p>
            </div>
          </div>
        </GlassCard>
      )}

      {/* Assets grouped by type */}
      {Object.keys(assetsByType).length > 0 && (
        <div className="space-y-4">
          {Object.entries(assetsByType).map(([type, assets]) => (
            <GlassCard key={type}>
              <div className="flex items-center gap-2 mb-3">
                <svg className="w-4 h-4 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d={ASSET_TYPE_ICONS[type] || ASSET_TYPE_ICONS.service} />
                </svg>
                <h3 className="text-sm font-semibold text-label-primary">
                  {ASSET_TYPE_LABELS[type] || type} <span className="text-label-tertiary font-normal">({assets.length})</span>
                </h3>
              </div>
              <div className="space-y-1">
                {assets.map((asset) => (
                  <div
                    key={asset.id}
                    className={`flex items-center justify-between px-3 py-2 rounded-ios transition-colors ${
                      asset.enabled ? 'bg-fill-secondary' : 'bg-fill-quaternary opacity-60'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <span className="text-sm text-label-primary font-medium truncate block">
                        {asset.display_name || asset.asset_name}
                      </span>
                      {asset.display_name && asset.display_name !== asset.asset_name && (
                        <span className="text-xs text-label-tertiary font-mono">{asset.asset_name}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-3">
                      {asset.baseline_value && Object.keys(asset.baseline_value).length > 0 && (
                        <span className="text-xs text-label-tertiary font-mono">
                          {Object.entries(asset.baseline_value).slice(0, 2).map(([k, v]) => `${k}=${String(v)}`).join(', ')}
                        </span>
                      )}
                      {(profile.status === 'discovered' || profile.status === 'draft') && (
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            checked={asset.enabled}
                            onChange={() => toggleMutation.mutate({ assetId: asset.id, enabled: !asset.enabled })}
                            className="sr-only peer"
                          />
                          <div className="w-9 h-5 bg-fill-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:bg-accent-primary after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all"></div>
                        </label>
                      )}
                      {asset.enabled && profile.status === 'active' && (
                        <svg className="w-4 h-4 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                        </svg>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      {/* Generated Rules */}
      {profile.rules.length > 0 && (
        <GlassCard>
          <h3 className="text-sm font-semibold text-label-primary mb-3">
            Generated L1 Rules <span className="text-label-tertiary font-normal">({profile.rules.length})</span>
          </h3>
          <div className="space-y-1 max-h-64 overflow-y-auto">
            {profile.rules.map((rule) => (
              <div key={rule.id} className="flex items-center justify-between px-3 py-1.5 bg-fill-secondary rounded-ios">
                <span className="text-xs font-mono text-accent-primary">{rule.l1_rule_id}</span>
                <span className={`text-xs ${rule.enabled ? 'text-health-healthy' : 'text-label-tertiary'}`}>
                  {rule.enabled ? 'Active' : 'Disabled'}
                </span>
              </div>
            ))}
          </div>
        </GlassCard>
      )}
    </div>
  );
};

export default ProtectionProfiles;

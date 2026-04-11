import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { GlassCard, Spinner, Badge, EmptyState, OnboardingChecklist } from '../components/shared';
import {
  SiteComplianceHero,
  SiteActivityTimeline,
  SiteSLAIndicator,
  SiteSearchBar,
  FloatingActionButton,
  type FloatingAction,
} from '../components/composed';
import { MeshHealthPanel } from '../components/composed/MeshHealthPanel';
import { DeploymentProgress } from '../components/deployment';
import { useSite, useAddCredential, useCreateApplianceOrder, useBroadcastOrder, useDeleteAppliance, useClearStaleAppliances, useUpdateHealingTier, useUpdateL2Mode } from '../hooks';
import type {
  SiteDeviceSummary,
  SiteWorkstationsResponse,
  SiteGoAgentsResponse,
  ProtectionProfileSummary,
  OrderType,
} from '../utils/api';
import { fleetUpdatesApi, type FleetStats } from '../utils/api';
import { ComplianceHealthInfographic } from '../client/ComplianceHealthInfographic';
import { DevicesAtRisk } from '../client/DevicesAtRisk';
import { getCsrfTokenOrEmpty } from '../utils/csrf';
import { ApplianceCard } from './site-detail/components/ApplianceCard';
import { OnboardingProgress } from './site-detail/components/OnboardingProgress';
import { SiteActionToolbar } from './site-detail/components/SiteActionToolbar';
import { SiteHeader } from './site-detail/components/SiteHeader';
import { EvidenceChainStatus } from './site-detail/components/EvidenceChainStatus';
import { AddCredentialModal } from './site-detail/modals/AddCredentialModal';
import { EditSiteModal } from './site-detail/modals/EditSiteModal';
import { MoveApplianceModal } from './site-detail/modals/MoveApplianceModal';
import { TransferApplianceModal } from './site-detail/modals/TransferApplianceModal';
import { DecommissionModal } from './site-detail/modals/DecommissionModal';
import { PortalLinkModal } from './site-detail/modals/PortalLinkModal';

/**
 * Site detail page
 */
export const SiteDetail: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const [showCredModal, setShowCredModal] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
  const [showPortalLinkModal, setShowPortalLinkModal] = useState(false);
  const [portalLink, setPortalLink] = useState<{ url: string; token: string } | null>(null);
  const [isGeneratingLink, setIsGeneratingLink] = useState(false);
  const [showEditSiteModal, setShowEditSiteModal] = useState(false);
  const [showMoveApplianceModal, setShowMoveApplianceModal] = useState<string | null>(null);
  const [showTransferModal, setShowTransferModal] = useState(false);
  const [showDecommissionModal, setShowDecommissionModal] = useState(false);
  const queryClient = useQueryClient();
  const { data: site, isLoading, error } = useSite(siteId || null);
  const { data: fleetStats } = useQuery<FleetStats>({
    queryKey: ['fleet-stats'],
    queryFn: fleetUpdatesApi.getStats,
    staleTime: 60_000,
  });
  const { data: coverageData } = useQuery<{
    network_coverage_pct: number;
    unmanaged_device_count: number;
  }>({
    queryKey: ['compliance-health-coverage', siteId],
    queryFn: async () => {
      const res = await fetch(`/api/dashboard/sites/${siteId}/compliance-health`, {
        credentials: 'same-origin',
      });
      if (!res.ok) return null;
      return res.json();
    },
    enabled: !!siteId,
    staleTime: 60_000,
    retry: false,
  });

  // Tab badge counts — lightweight summary queries. Each is isolated from the
  // main site fetch so a 404/error on one endpoint never hides the whole page.
  const { data: deviceSummary } = useQuery<SiteDeviceSummary | null>({
    queryKey: ['site-device-summary', siteId],
    queryFn: async () => {
      try {
        const res = await fetch(`/api/dashboard/devices/sites/${siteId}/summary`, {
          credentials: 'same-origin',
        });
        if (!res.ok) return null;
        return res.json();
      } catch {
        return null;
      }
    },
    enabled: !!siteId,
    staleTime: 60_000,
    retry: false,
  });

  const { data: workstationSummary } = useQuery<SiteWorkstationsResponse | null>({
    queryKey: ['site-workstation-summary', siteId],
    queryFn: async () => {
      try {
        const res = await fetch(`/api/dashboard/sites/${siteId}/workstations`, {
          credentials: 'same-origin',
        });
        if (!res.ok) return null;
        return res.json();
      } catch {
        return null;
      }
    },
    enabled: !!siteId,
    staleTime: 60_000,
    retry: false,
  });

  const { data: agentSummary } = useQuery<SiteGoAgentsResponse | null>({
    queryKey: ['site-agent-summary', siteId],
    queryFn: async () => {
      try {
        const res = await fetch(`/api/dashboard/sites/${siteId}/agents`, {
          credentials: 'same-origin',
        });
        if (!res.ok) return null;
        return res.json();
      } catch {
        return null;
      }
    },
    enabled: !!siteId,
    staleTime: 60_000,
    retry: false,
  });

  const { data: protectionProfiles } = useQuery<ProtectionProfileSummary[] | null>({
    queryKey: ['site-protection-profiles', siteId],
    queryFn: async () => {
      try {
        const res = await fetch(`/api/dashboard/protection-profiles?site_id=${siteId}`, {
          credentials: 'same-origin',
        });
        if (!res.ok) return null;
        return res.json();
      } catch {
        return null;
      }
    },
    enabled: !!siteId,
    staleTime: 60_000,
    retry: false,
  });

  // Derived tab counts — null means "don't render a badge".
  const deviceTabCount: number | null = deviceSummary?.total_devices ?? null;
  const workstationTabCount: number | null = workstationSummary?.summary?.total_workstations ?? null;
  const agentTabCount: number | null = agentSummary?.summary?.total_agents ?? null;
  const protectionTabCount: number | null = Array.isArray(protectionProfiles) ? protectionProfiles.length : null;

  const latestVersion = fleetStats?.releases.latest_version ?? null;

  // WireGuard VPN connection status — connected if handshake within last 5 minutes
  const isWgConnected = (() => {
    if (!site?.wg_connected_at) return false;
    const connectedAt = new Date(site.wg_connected_at);
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000);
    return connectedAt > fiveMinAgo;
  })();

  const addCredential = useAddCredential();
  const createOrder = useCreateApplianceOrder();
  const broadcastOrder = useBroadcastOrder();
  const deleteAppliance = useDeleteAppliance();
  const clearStale = useClearStaleAppliances();
  const updateHealingTier = useUpdateHealingTier();
  const updateL2Mode = useUpdateL2Mode();

  const isOrderLoading = createOrder.isPending || broadcastOrder.isPending || deleteAppliance.isPending || clearStale.isPending;

  // Show toast notification
  const showToast = (message: string, type: 'success' | 'error') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  // Generate portal link for client access
  const handleGeneratePortalLink = async () => {
    if (!siteId) return;
    setIsGeneratingLink(true);
    try {
      const response = await fetch(`/api/portal/sites/${siteId}/generate-token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!response.ok) {
        throw new Error('Failed to generate portal link');
      }
      const data = await response.json();
      setPortalLink({ url: data.portal_url, token: data.token });
      setShowPortalLinkModal(true);
    } catch (error) {
      showToast(`Failed to generate portal link: ${error instanceof Error ? error.message : String(error)}`, 'error');
    } finally {
      setIsGeneratingLink(false);
    }
  };

  // Handle creating an order for a specific appliance
  const handleCreateOrder = async (applianceId: string, orderType: OrderType, parameters?: Record<string, unknown>) => {
    if (!siteId) return;
    try {
      await createOrder.mutateAsync({ siteId, applianceId, orderType, parameters });
      showToast(`Order "${orderType}" sent to appliance`, 'success');
    } catch (error) {
      showToast(`Failed to create order: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle broadcasting an order to all appliances
  const handleBroadcast = async (orderType: OrderType) => {
    if (!siteId) return;
    try {
      const result = await broadcastOrder.mutateAsync({ siteId, orderType });
      showToast(`Order "${orderType}" broadcast to ${result.length} appliances`, 'success');
    } catch (error) {
      showToast(`Failed to broadcast order: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle deleting an appliance
  const handleDeleteAppliance = async (applianceId: string) => {
    if (!siteId) return;
    try {
      await deleteAppliance.mutateAsync({ siteId, applianceId });
      showToast('Appliance deleted', 'success');
    } catch (error) {
      showToast(`Failed to delete appliance: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle clearing stale appliances
  const handleClearStale = async () => {
    if (!siteId) return;
    try {
      const result = await clearStale.mutateAsync({ siteId, staleHours: 24 });
      showToast(`Cleared ${result.deleted_count} stale appliances`, 'success');
    } catch (error) {
      showToast(`Failed to clear stale appliances: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle updating L2 mode for an appliance
  const handleUpdateL2Mode = async (applianceId: string, l2Mode: string) => {
    if (!siteId) return;
    try {
      await updateL2Mode.mutateAsync({ siteId, applianceId, l2Mode });
      const labels: Record<string, string> = { auto: 'Auto', manual: 'Manual', disabled: 'Disabled' };
      showToast(`L2 healing set to ${labels[l2Mode] || l2Mode}`, 'success');
    } catch (error) {
      showToast(`Failed to update L2 mode: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle updating healing tier
  const handleHealingTierChange = async (tier: 'standard' | 'full_coverage') => {
    if (!siteId) return;
    try {
      await updateHealingTier.mutateAsync({ siteId, healingTier: tier });
      showToast(`Healing tier updated to ${tier === 'full_coverage' ? 'Full Coverage' : 'Standard'}`, 'success');
    } catch (error) {
      showToast(`Failed to update healing tier: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner size="lg" />
      </div>
    );
  }

  if (error || !site) {
    return (
      <GlassCard className="text-center py-12">
        <h2 className="text-xl font-semibold text-label-primary mb-2">Site Not Found</h2>
        <p className="text-label-tertiary mb-4">The site "{siteId}" could not be found.</p>
        <button onClick={() => navigate('/sites')} className="btn-primary">
          Back to Sites
        </button>
      </GlassCard>
    );
  }

  // Handle moving an appliance to a different site
  const handleMoveAppliance = async (applianceId: string, targetSiteId: string) => {
    if (!siteId) return;
    try {
      const res = await fetch(`/api/sites/${siteId}/appliances/${applianceId}/move`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfTokenOrEmpty() },
        body: JSON.stringify({ target_site_id: targetSiteId }),
      });
      if (res.ok) {
        showToast('Appliance moved successfully', 'success');
        setShowMoveApplianceModal(null);
        queryClient.invalidateQueries({ queryKey: ['site', siteId] });
      } else {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        showToast(`Failed to move appliance: ${err.detail}`, 'error');
      }
    } catch {
      showToast('Failed to move appliance', 'error');
    }
  };

  // Quick-action launcher fan-out — rendered by the FloatingActionButton.
  // Covers the most common tasks operators reach for on a live site:
  // force rescan, add a credential, broadcast a fleet update, open
  // the run-a-runbook flow. Destructive/slow actions stay in the More menu.
  const fabActions: FloatingAction[] = [
    {
      label: 'Force Rescan',
      tone: 'primary',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      ),
      onClick: () => handleBroadcast('run_drift'),
    },
    {
      label: 'Add Credential',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
        </svg>
      ),
      onClick: () => setShowCredModal(true),
    },
    {
      label: 'Check for Updates',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M16 12l-4 4m0 0l-4-4m4 4V4" />
        </svg>
      ),
      onClick: () => handleBroadcast('force_checkin'),
    },
    {
      label: 'Open Runbooks',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
        </svg>
      ),
      onClick: () => navigate(`/runbooks?site_id=${siteId}`),
    },
  ];

  const handleAddCredential = async (data: Parameters<typeof addCredential.mutate>[0]['data']) => {
    try {
      await addCredential.mutateAsync({ siteId: site.site_id, data });
      setShowCredModal(false);
    } catch (error) {
      console.error('Failed to add credential:', error);
    }
  };

  return (
    <div className="space-y-6">
      <SiteHeader
        site={site}
        siteId={siteId}
        isWgConnected={isWgConnected}
        isGeneratingLink={isGeneratingLink}
        deviceTabCount={deviceTabCount}
        workstationTabCount={workstationTabCount}
        agentTabCount={agentTabCount}
        protectionTabCount={protectionTabCount}
        onEditSite={() => setShowEditSiteModal(true)}
        onGeneratePortalLink={handleGeneratePortalLink}
        onTransferAppliance={() => setShowTransferModal(true)}
        onDecommissionSite={() => setShowDecommissionModal(true)}
      />

      {/* In-site search bar — searches incidents, devices, creds, workstations */}
      {siteId && <SiteSearchBar siteId={siteId} />}

      {/* Site Compliance Hero — pinned headline summary */}
      {site && <SiteComplianceHero site={site} />}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main content */}
        <div className="lg:col-span-2 space-y-6">
          {/* Deployment Progress (Zero-Friction Pipeline) */}
          {siteId && <DeploymentProgress siteId={siteId} site={site} />}

          {/* Compliance Health Infographic */}
          {siteId && site && (
            <ComplianceHealthInfographic
              sites={[{ site_id: site.site_id, clinic_name: site.clinic_name }]}
              apiPrefix="/api/dashboard"
              onCategoryClick={(category, sid) => navigate(`/incidents?site_id=${sid}&category=${category}`)}
              onStatusClick={(status, sid) => navigate(`/incidents?site_id=${sid}&status=${status}`)}
            />
          )}

          {/* Devices at Risk */}
          {siteId && site && (
            <DevicesAtRisk
              siteId={site.site_id}
              apiPrefix="/api/dashboard"
              onDeviceClick={(hostname, sid) => navigate(`/incidents?site_id=${sid}&hostname=${hostname}`)}
            />
          )}

          {/* Contact Information */}
          <GlassCard>
            <h2 className="text-lg font-semibold mb-4">Contact Information</h2>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-label-tertiary text-sm">Contact Name</p>
                <p className="text-label-primary">{site.contact_name || '-'}</p>
              </div>
              <div>
                <p className="text-label-tertiary text-sm">Email</p>
                <p className="text-label-primary">{site.contact_email || '-'}</p>
              </div>
              <div>
                <p className="text-label-tertiary text-sm">Phone</p>
                <p className="text-label-primary">{site.contact_phone || '-'}</p>
              </div>
              <div>
                <p className="text-label-tertiary text-sm">Tier</p>
                <Badge variant={site.tier === 'large' ? 'success' : site.tier === 'mid' ? 'info' : 'default'}>
                  {site.tier?.replace(/\b\w/g, c => c.toUpperCase()) || 'Standard'}
                </Badge>
              </div>
              <div>
                <p className="text-label-tertiary text-sm">Healing Mode</p>
                <div className="flex items-center gap-2">
                  <select
                    value={site.healing_tier || 'standard'}
                    onChange={(e) => handleHealingTierChange(e.target.value as 'standard' | 'full_coverage')}
                    disabled={updateHealingTier.isPending}
                    className="px-2 py-1 text-sm rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:outline-none focus:ring-2 focus:ring-accent-primary disabled:opacity-50"
                  >
                    <option value="standard">Standard</option>
                    <option value="full_coverage">Full Coverage</option>
                  </select>
                  {updateHealingTier.isPending && (
                    <Spinner size="sm" />
                  )}
                </div>
              </div>
              {site.address && (
                <div className="col-span-2">
                  <p className="text-label-tertiary text-sm">Address</p>
                  <p className="text-label-primary">{site.address}</p>
                </div>
              )}
            </div>
          </GlassCard>

          {/* Mesh health panel — only renders for multi-appliance sites */}
          <MeshHealthPanel siteId={siteId || ''} />

          {/* Appliances */}
          <GlassCard>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">
                Appliances ({site.appliances.length})
              </h2>
              <button
                onClick={() => {
                  const mac = prompt('Enter the appliance MAC address (e.g., 84:3A:5B:1F:FF:E4).\n\nFound on the device label or BIOS POST screen.');
                  if (!mac) return;
                  fetch(`/api/dashboard/sites/${siteId}/provision`, {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': document.cookie.match(/csrf_token=([^;]+)/)?.[1] || '' },
                    body: JSON.stringify({ mac_address: mac.trim() }),
                  })
                    .then(r => r.json())
                    .then(data => {
                      setToast({ message: data.message || 'Provision created', type: 'success' });
                    })
                    .catch(() => setToast({ message: 'Failed to create provision', type: 'error' }));
                }}
                className="text-sm font-medium px-3 py-1.5 rounded-lg text-white transition-all"
                style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
              >
                + Provision Appliance
              </button>
            </div>

            <SiteActionToolbar
              applianceCount={site.appliances.length}
              onBroadcast={handleBroadcast}
              onClearStale={handleClearStale}
              isLoading={isOrderLoading}
            />
            {site.appliances.length === 0 ? (
              <EmptyState
                icon={
                  <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
                  </svg>
                }
                title="No appliances connected"
                description="The appliance will appear here automatically once it is installed and phones home to Central Command."
              />
            ) : (
              <div className="space-y-4">
                {site.appliances.map((appliance) => (
                  <ApplianceCard
                    key={appliance.appliance_id}
                    appliance={appliance}
                    latestVersion={latestVersion}
                    onCreateOrder={handleCreateOrder}
                    onDelete={handleDeleteAppliance}
                    onUpdateL2Mode={handleUpdateL2Mode}
                    onMove={(id) => setShowMoveApplianceModal(id)}
                    isLoading={isOrderLoading}
                    applianceCount={site.appliances.length}
                  />
                ))}
              </div>
            )}
          </GlassCard>

          {/* Evidence Chain Status */}
          {siteId && <EvidenceChainStatus siteId={siteId} />}

          {/* Credentials */}
          <GlassCard>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Credentials ({site.credentials.length})</h2>
              <button
                onClick={() => setShowCredModal(true)}
                className="text-sm text-accent-primary hover:text-accent-primary/80"
              >
                + Add Credential
              </button>
            </div>
            {site.credentials.length === 0 ? (
              <EmptyState
                icon={
                  <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                  </svg>
                }
                title="No credentials configured"
                description="Add router, Active Directory, or other credentials so the appliance can perform deep compliance scans."
                action={{
                  label: 'Add credential',
                  onClick: () => setShowCredModal(true),
                }}
              />
            ) : (
              <div className="space-y-2">
                {site.credentials.map((cred) => (
                  <div
                    key={cred.id}
                    className="flex items-center justify-between py-3 px-4 bg-fill-secondary rounded-ios"
                  >
                    <div>
                      <p className="font-medium text-label-primary">{cred.credential_name}</p>
                      <p className="text-xs text-label-tertiary">{cred.credential_type}</p>
                    </div>
                    <Badge variant="default">Stored</Badge>
                  </div>
                ))}
              </div>
            )}
          </GlassCard>
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* Healing SLA — target vs current + 7-day trend sparkline */}
          {siteId && <SiteSLAIndicator siteId={siteId} />}

          {/* Recent Activity — admin audit + fleet orders + incidents */}
          {siteId && <SiteActivityTimeline siteId={siteId} />}

          {/* Network Visibility */}
          {coverageData !== null && coverageData !== undefined && (
            <GlassCard>
              <h2 className="text-lg font-semibold mb-3">Network Visibility</h2>
              <div className="bg-white/5 rounded-lg p-4">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-sm text-label-secondary">Devices Discovered</div>
                  <div className="text-lg font-bold text-label-primary">
                    {coverageData.unmanaged_device_count ?? 0}
                  </div>
                </div>
                <div className="text-xs text-label-tertiary">
                  Appliance continuously scans the network and reports all discovered devices. Add credentials to enable deep compliance scanning on servers and workstations.
                </div>
              </div>
            </GlassCard>
          )}

          {/* Onboarding Checklist — shows for new sites that aren't fully active yet */}
          {(site.onboarding_stage !== 'active' || (site.timestamps.baseline_at === null && site.timestamps.scanning_at === null)) && (
            <GlassCard>
              <OnboardingChecklist
                site={site}
                onNavigateDevices={() => navigate(`/sites/${siteId}/devices`)}
                onAddCredential={() => setShowCredModal(true)}
              />
            </GlassCard>
          )}

          {/* Onboarding Progress */}
          <GlassCard>
            <h2 className="text-lg font-semibold mb-4">Onboarding Progress</h2>
            <OnboardingProgress timestamps={site.timestamps} stage={site.onboarding_stage} />
          </GlassCard>

          {/* Notes */}
          {site.notes && (
            <GlassCard>
              <h2 className="text-lg font-semibold mb-2">Notes</h2>
              <p className="text-label-secondary text-sm">{site.notes}</p>
            </GlassCard>
          )}

          {/* Blockers */}
          {site.blockers.length > 0 && (
            <GlassCard className="border-l-4 border-health-warning">
              <h2 className="text-lg font-semibold mb-2">Blockers</h2>
              <ul className="space-y-2">
                {site.blockers.map((blocker, i) => (
                  <li key={i} className="text-sm text-label-secondary flex items-start gap-2">
                    <span className="text-health-warning">!</span>
                    {blocker}
                  </li>
                ))}
              </ul>
            </GlassCard>
          )}

          {/* Tracking */}
          {site.tracking_number && (
            <GlassCard>
              <h2 className="text-lg font-semibold mb-2">Shipping</h2>
              <p className="text-label-tertiary text-sm">Carrier</p>
              <p className="text-label-primary">{site.tracking_carrier || 'Unknown'}</p>
              <p className="text-label-tertiary text-sm mt-2">Tracking Number</p>
              <p className="text-label-primary font-mono text-sm">{site.tracking_number}</p>
            </GlassCard>
          )}
        </div>
      </div>

      {/* Add Credential Modal */}
      <AddCredentialModal
        isOpen={showCredModal}
        onClose={() => setShowCredModal(false)}
        onSubmit={handleAddCredential}
        isLoading={addCredential.isPending}
      />

      {/* Portal Link Modal */}
      {showPortalLinkModal && portalLink && (
        <PortalLinkModal
          portalLink={portalLink}
          onClose={() => setShowPortalLinkModal(false)}
          showToast={showToast}
        />
      )}

      {/* Edit Site Modal */}
      {showEditSiteModal && site && (
        <EditSiteModal
          site={site}
          onClose={() => setShowEditSiteModal(false)}
          onSaved={() => { setShowEditSiteModal(false); queryClient.invalidateQueries({ queryKey: ['site', siteId] }); }}
          showToast={showToast}
        />
      )}

      {/* Move Appliance Modal */}
      {showMoveApplianceModal && siteId && (
        <MoveApplianceModal
          applianceId={showMoveApplianceModal}
          currentSiteId={siteId}
          onClose={() => setShowMoveApplianceModal(null)}
          onMove={handleMoveAppliance}
        />
      )}

      {/* Transfer Appliance Modal */}
      {showTransferModal && site && siteId && (
        <TransferApplianceModal
          appliances={site.appliances}
          currentSiteId={siteId}
          onClose={() => setShowTransferModal(false)}
          onTransferred={() => {
            setShowTransferModal(false);
            queryClient.invalidateQueries({ queryKey: ['site', siteId] });
          }}
          showToast={showToast}
        />
      )}

      {/* Decommission Modal */}
      {showDecommissionModal && site && (
        <DecommissionModal
          site={site}
          onClose={() => setShowDecommissionModal(false)}
          onDecommissioned={() => {
            setShowDecommissionModal(false);
            navigate('/sites');
          }}
          showToast={showToast}
        />
      )}

      {/* Toast Notification */}
      {toast && (
        <div
          className={`fixed bottom-4 right-4 px-4 py-3 rounded-ios shadow-lg z-50 ${
            toast.type === 'success' ? 'bg-health-healthy text-white' : 'bg-health-critical text-white'
          }`}
        >
          {toast.message}
        </div>
      )}

      {/* Floating quick-action launcher — viewport-anchored FAB */}
      {site && siteId && site.status !== 'inactive' && (
        <FloatingActionButton
          ariaLabel="Site quick actions"
          actions={fabActions}
        />
      )}
    </div>
  );
};

export default SiteDetail;

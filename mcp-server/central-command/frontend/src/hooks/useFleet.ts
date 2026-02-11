/**
 * React hooks for fleet data fetching
 */

import { useQuery, useQueryClient, useMutation, keepPreviousData } from '@tanstack/react-query';
import { fleetApi, incidentApi, statsApi, learningApi, runbookApi, onboardingApi, sitesApi, ordersApi, notificationsApi, runbookConfigApi, workstationsApi, goAgentsApi, devicesApi, cveApi, frameworkSyncApi } from '../utils/api';
import type { Site, SiteDetail, OrderType, OrderResponse, RunbookCatalogItem, SiteRunbookConfig, SiteWorkstationsResponse, SiteGoAgentsResponse, SiteDevicesResponse, SiteDeviceSummary, DiscoveredDevice } from '../utils/api';
import type { ClientOverview, ClientDetail, Incident, ComplianceEvent, GlobalStats, LearningStatus, PromotionCandidate, PromotionHistory, Runbook, RunbookDetail, RunbookExecution, OnboardingClient, OnboardingMetrics, Notification, NotificationSummary, CVESummary, CVEEntry, CVEDetail, CVEWatchConfig, FrameworkSyncStatus, FrameworkControl, CoverageAnalysis, FrameworkCategory } from '../types';
import { useWebSocketStatus } from './useWebSocket';

// Polling intervals
const POLLING_INTERVAL = 60_000;      // Fallback when WebSocket is disconnected
const STALE_TIME = 30_000;            // Consider data fresh for 30 seconds

/**
 * Returns common query options that disable polling when WebSocket is active.
 * When WS is connected, real-time events push updates via cache invalidation,
 * so polling is unnecessary and causes race conditions.
 */
function useQueryDefaults() {
  const { connected } = useWebSocketStatus();
  return {
    // Disable polling when WebSocket pushes real-time updates
    refetchInterval: connected ? false as const : POLLING_INTERVAL,
    staleTime: STALE_TIME,
    placeholderData: keepPreviousData,
  };
}

// NOTE: React Query provides an AbortSignal through queryFn context.
// To enable request cancellation, update API functions to accept { signal } options
// and pass it like: queryFn: ({ signal }) => api.getData({ signal })
// The fetchApi/fetchSitesApi functions already support signal via FetchApiOptions.

/**
 * Helper to log mutation errors consistently
 */
function logMutationError(context: string, error: unknown): void {
  // Skip logging for aborted requests
  if (error instanceof Error && error.message.includes('cancelled')) {
    return;
  }
  console.error(`Mutation error (${context}):`, error);
}

/**
 * Hook for fetching fleet overview data with polling
 */
export function useFleet() {
  const defaults = useQueryDefaults();
  return useQuery<ClientOverview[]>({
    queryKey: ['fleet'],
    queryFn: fleetApi.getFleet,
    ...defaults,
  });
}

/**
 * Hook for fetching a single client's details
 */
export function useClient(siteId: string | null) {
  const defaults = useQueryDefaults();
  return useQuery<ClientDetail>({
    queryKey: ['client', siteId],
    queryFn: () => fleetApi.getClient(siteId!),
    enabled: !!siteId,
    ...defaults,
  });
}

/**
 * Hook for fetching recent incidents with optional filters
 */
export function useIncidents(params?: {
  site_id?: string;
  limit?: number;
  level?: string;
  resolved?: boolean;
}) {
  const defaults = useQueryDefaults();
  return useQuery<Incident[]>({
    queryKey: ['incidents', params],
    queryFn: () => incidentApi.getIncidents(params),
    ...defaults,
  });
}

/**
 * Hook for fetching compliance events (drift detections from compliance bundles)
 */
export function useEvents(params?: {
  site_id?: string;
  limit?: number;
}) {
  const defaults = useQueryDefaults();
  return useQuery<ComplianceEvent[]>({
    queryKey: ['events', params],
    queryFn: () => incidentApi.getEvents(params),
    ...defaults,
  });
}

/**
 * Hook for fetching global stats
 */
export function useGlobalStats() {
  const defaults = useQueryDefaults();
  return useQuery<GlobalStats>({
    queryKey: ['stats'],
    queryFn: statsApi.getGlobalStats,
    ...defaults,
  });
}

/**
 * Hook for fetching learning loop status
 */
export function useLearningStatus() {
  const defaults = useQueryDefaults();
  return useQuery<LearningStatus>({
    queryKey: ['learning', 'status'],
    queryFn: learningApi.getStatus,
    ...defaults,
  });
}

/**
 * Hook to manually trigger a refresh of fleet data
 */
export function useRefreshFleet() {
  const queryClient = useQueryClient();

  return () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['fleet'] }),
    queryClient.invalidateQueries({ queryKey: ['incidents'] }),
    queryClient.invalidateQueries({ queryKey: ['stats'] }),
    queryClient.invalidateQueries({ queryKey: ['learning'] }),
    queryClient.invalidateQueries({ queryKey: ['runbooks'] }),
  ]);
}

/**
 * Hook for fetching all runbooks
 */
export function useRunbooks() {
  return useQuery<Runbook[]>({
    queryKey: ['runbooks'],
    queryFn: runbookApi.getRunbooks,
    staleTime: STALE_TIME,
    placeholderData: keepPreviousData,
  });
}

/**
 * Hook for fetching a single runbook's details
 */
export function useRunbook(runbookId: string | null) {
  return useQuery<RunbookDetail>({
    queryKey: ['runbook', runbookId],
    queryFn: () => runbookApi.getRunbook(runbookId!),
    enabled: !!runbookId,
    staleTime: STALE_TIME,
    placeholderData: keepPreviousData,
  });
}

/**
 * Hook for fetching runbook executions
 */
export function useRunbookExecutions(runbookId: string | null, limit?: number) {
  return useQuery<RunbookExecution[]>({
    queryKey: ['runbook', runbookId, 'executions', limit],
    queryFn: () => runbookApi.getExecutions(runbookId!, limit),
    enabled: !!runbookId,
    staleTime: STALE_TIME,
    placeholderData: keepPreviousData,
  });
}

/**
 * Hook for fetching promotion candidates
 */
export function usePromotionCandidates() {
  const defaults = useQueryDefaults();
  return useQuery<PromotionCandidate[]>({
    queryKey: ['learning', 'candidates'],
    queryFn: learningApi.getCandidates,
    ...defaults,
  });
}

/**
 * Hook for fetching promotion history
 */
export function usePromotionHistory(limit?: number) {
  const defaults = useQueryDefaults();
  return useQuery<PromotionHistory[]>({
    queryKey: ['learning', 'history', limit],
    queryFn: () => learningApi.getHistory(limit),
    ...defaults,
  });
}

/**
 * Hook for promoting a pattern to L1
 */
export function usePromotePattern() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (patternId: string) => learningApi.promote(patternId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['learning'] });
    },
    onError: (error) => logMutationError('promotePattern', error),
  });
}

/**
 * Hook for rejecting a promotion candidate
 */
export function useRejectPattern() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (patternId: string) => learningApi.reject(patternId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['learning'] });
    },
    onError: (error) => logMutationError('rejectPattern', error),
  });
}

/**
 * Hook for fetching onboarding pipeline
 */
export function useOnboardingPipeline() {
  const defaults = useQueryDefaults();
  return useQuery<OnboardingClient[]>({
    queryKey: ['onboarding', 'pipeline'],
    queryFn: onboardingApi.getPipeline,
    ...defaults,
  });
}

/**
 * Hook for fetching onboarding metrics
 */
export function useOnboardingMetrics() {
  const defaults = useQueryDefaults();
  return useQuery<OnboardingMetrics>({
    queryKey: ['onboarding', 'metrics'],
    queryFn: onboardingApi.getMetrics,
    ...defaults,
  });
}

// =============================================================================
// SITES HOOKS (Real appliance onboarding data)
// =============================================================================

/**
 * Hook for fetching all sites with live status
 */
export function useSites(status?: string) {
  const defaults = useQueryDefaults();
  return useQuery<{ sites: Site[]; count: number }>({
    queryKey: ['sites', status],
    queryFn: () => sitesApi.getSites(status),
    ...defaults,
  });
}

/**
 * Hook for fetching a single site's details
 */
export function useSite(siteId: string | null) {
  const defaults = useQueryDefaults();
  return useQuery<SiteDetail>({
    queryKey: ['site', siteId],
    queryFn: () => sitesApi.getSite(siteId!),
    enabled: !!siteId,
    ...defaults,
  });
}

/**
 * Hook for creating a new site
 */
export function useCreateSite() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: sitesApi.createSite,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sites'] });
    },
    onError: (error) => logMutationError('createSite', error),
  });
}

/**
 * Hook for updating a site
 */
export function useUpdateSite() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, data }: { siteId: string; data: Parameters<typeof sitesApi.updateSite>[1] }) =>
      sitesApi.updateSite(siteId, data),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['sites'] });
      queryClient.invalidateQueries({ queryKey: ['site', siteId] });
    },
    onError: (error) => logMutationError('updateSite', error),
  });
}

/**
 * Hook for adding credentials to a site
 */
export function useAddCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, data }: { siteId: string; data: Parameters<typeof sitesApi.addCredential>[1] }) =>
      sitesApi.addCredential(siteId, data),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['site', siteId] });
    },
    onError: (error) => logMutationError('addCredential', error),
  });
}

/**
 * Hook for deleting a site
 */
export function useDeleteSite() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (siteId: string) => sitesApi.deleteSite(siteId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sites'] });
      queryClient.invalidateQueries({ queryKey: ['fleet'] });
    },
    onError: (error) => logMutationError('deleteSite', error),
  });
}

/**
 * Hook for deleting a credential from a site
 */
export function useDeleteCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, credentialId }: { siteId: string; credentialId: string }) =>
      sitesApi.deleteCredential(siteId, credentialId),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['site', siteId] });
    },
    onError: (error) => logMutationError('deleteCredential', error),
  });
}

/**
 * Hook for updating healing tier for a site
 */
export function useUpdateHealingTier() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, healingTier }: { siteId: string; healingTier: 'standard' | 'full_coverage' }) =>
      sitesApi.updateHealingTier(siteId, healingTier),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['sites'] });
      queryClient.invalidateQueries({ queryKey: ['site', siteId] });
    },
    onError: (error) => logMutationError('updateHealingTier', error),
  });
}

// =============================================================================
// ORDER HOOKS
// =============================================================================

/**
 * Hook for fetching orders for a site
 */
export function useOrders(siteId: string | null, status?: string) {
  const defaults = useQueryDefaults();
  return useQuery<OrderResponse[]>({
    queryKey: ['orders', siteId, status],
    queryFn: () => ordersApi.getOrders(siteId!, status as any),
    enabled: !!siteId,
    ...defaults,
  });
}

/**
 * Hook for creating an order for a specific appliance
 */
export function useCreateApplianceOrder() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      siteId,
      applianceId,
      orderType,
      parameters = {},
      priority = 0,
    }: {
      siteId: string;
      applianceId: string;
      orderType: OrderType;
      parameters?: Record<string, unknown>;
      priority?: number;
    }) =>
      ordersApi.createApplianceOrder(siteId, applianceId, {
        order_type: orderType,
        parameters,
        priority,
      }),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['orders', siteId] });
      queryClient.invalidateQueries({ queryKey: ['site', siteId] });
    },
    onError: (error) => logMutationError('createApplianceOrder', error),
  });
}

/**
 * Hook for broadcasting an order to all appliances in a site
 */
export function useBroadcastOrder() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      siteId,
      orderType,
      parameters = {},
    }: {
      siteId: string;
      orderType: OrderType;
      parameters?: Record<string, unknown>;
    }) =>
      ordersApi.broadcastOrder(siteId, {
        order_type: orderType,
        parameters,
      }),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['orders', siteId] });
      queryClient.invalidateQueries({ queryKey: ['site', siteId] });
    },
    onError: (error) => logMutationError('broadcastOrder', error),
  });
}

/**
 * Hook for deleting an appliance
 */
export function useDeleteAppliance() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, applianceId }: { siteId: string; applianceId: string }) =>
      ordersApi.deleteAppliance(siteId, applianceId),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['site', siteId] });
      queryClient.invalidateQueries({ queryKey: ['sites'] });
    },
    onError: (error) => logMutationError('deleteAppliance', error),
  });
}

/**
 * Hook for clearing stale appliances from a site
 */
export function useClearStaleAppliances() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, staleHours = 24 }: { siteId: string; staleHours?: number }) =>
      ordersApi.clearStaleAppliances(siteId, staleHours),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['site', siteId] });
      queryClient.invalidateQueries({ queryKey: ['sites'] });
    },
    onError: (error) => logMutationError('clearStaleAppliances', error),
  });
}

// =============================================================================
// NOTIFICATION HOOKS
// =============================================================================

/**
 * Hook for fetching notifications
 */
export function useNotifications(params?: {
  site_id?: string;
  severity?: string;
  unread_only?: boolean;
  limit?: number;
}) {
  const defaults = useQueryDefaults();
  return useQuery<Notification[]>({
    queryKey: ['notifications', params],
    queryFn: () => notificationsApi.getNotifications(params),
    ...defaults,
  });
}

/**
 * Hook for fetching notification summary (counts)
 */
export function useNotificationSummary() {
  const defaults = useQueryDefaults();
  return useQuery<NotificationSummary>({
    queryKey: ['notifications', 'summary'],
    queryFn: notificationsApi.getSummary,
    ...defaults,
  });
}

/**
 * Hook for marking a notification as read
 */
export function useMarkNotificationRead() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (notificationId: string) => notificationsApi.markRead(notificationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
    },
    onError: (error) => logMutationError('markNotificationRead', error),
  });
}

/**
 * Hook for marking all notifications as read
 */
export function useMarkAllNotificationsRead() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => notificationsApi.markAllRead(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
    },
    onError: (error) => logMutationError('markAllNotificationsRead', error),
  });
}

/**
 * Hook for dismissing a notification
 */
export function useDismissNotification() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (notificationId: string) => notificationsApi.dismiss(notificationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
    },
    onError: (error) => logMutationError('dismissNotification', error),
  });
}

/**
 * Hook for creating a notification
 */
export function useCreateNotification() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (notification: {
      severity: 'critical' | 'warning' | 'info' | 'success';
      category: string;
      title: string;
      message: string;
      site_id?: string;
      appliance_id?: string;
      metadata?: Record<string, unknown>;
    }) => notificationsApi.create(notification),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
    },
    onError: (error) => logMutationError('createNotification', error),
  });
}

// =============================================================================
// RUNBOOK CONFIG HOOKS (Partner-configurable runbook enable/disable)
// =============================================================================

/**
 * Hook for fetching all runbooks in the catalog
 */
export function useRunbookCatalog() {
  return useQuery<RunbookCatalogItem[]>({
    queryKey: ['runbookCatalog'],
    queryFn: runbookConfigApi.getRunbooks,
    staleTime: STALE_TIME,
    placeholderData: keepPreviousData,
  });
}

/**
 * Hook for fetching runbook categories
 */
export function useRunbookCategories() {
  return useQuery<string[]>({
    queryKey: ['runbookCategories'],
    queryFn: runbookConfigApi.getCategories,
    staleTime: STALE_TIME,
    placeholderData: keepPreviousData,
  });
}

/**
 * Hook for fetching a site's runbook configuration
 */
export function useSiteRunbookConfig(siteId: string | null) {
  return useQuery<SiteRunbookConfig[]>({
    queryKey: ['siteRunbooks', siteId],
    queryFn: () => runbookConfigApi.getSiteRunbooks(siteId!),
    enabled: !!siteId,
    staleTime: STALE_TIME,
    placeholderData: keepPreviousData,
  });
}

/**
 * Hook for enabling/disabling a runbook for a site
 */
export function useSetSiteRunbook() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, runbookId, enabled }: { siteId: string; runbookId: string; enabled: boolean }) =>
      runbookConfigApi.setSiteRunbook(siteId, runbookId, enabled),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['siteRunbooks', siteId] });
    },
    onError: (error) => logMutationError('setSiteRunbook', error),
  });
}

// =============================================================================
// WORKSTATION HOOKS (Site workstation compliance monitoring)
// =============================================================================

/**
 * Hook for fetching workstations for a site with summary
 */
export function useSiteWorkstations(siteId: string | null) {
  const defaults = useQueryDefaults();
  return useQuery<SiteWorkstationsResponse>({
    queryKey: ['workstations', siteId],
    queryFn: () => workstationsApi.getSiteWorkstations(siteId!),
    enabled: !!siteId,
    ...defaults,
  });
}

/**
 * Hook for triggering a workstation scan
 */
export function useTriggerWorkstationScan() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (siteId: string) => workstationsApi.triggerScan(siteId),
    onSuccess: (_, siteId) => {
      queryClient.invalidateQueries({ queryKey: ['workstations', siteId] });
    },
    onError: (error) => logMutationError('triggerWorkstationScan', error),
  });
}

// =============================================================================
// GO AGENTS HOOKS (Workstation-scale gRPC agents)
// =============================================================================

/**
 * Hook for fetching Go agents for a site with summary
 */
export function useSiteGoAgents(siteId: string | null) {
  const defaults = useQueryDefaults();
  return useQuery<SiteGoAgentsResponse>({
    queryKey: ['goAgents', siteId],
    queryFn: () => goAgentsApi.getSiteAgents(siteId!),
    enabled: !!siteId,
    ...defaults,
  });
}

/**
 * Hook for updating Go agent capability tier
 */
export function useUpdateGoAgentTier() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, agentId, tier }: {
      siteId: string;
      agentId: string;
      tier: 'monitor_only' | 'self_heal' | 'full_remediation';
    }) => goAgentsApi.updateTier(siteId, agentId, tier),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['goAgents', siteId] });
    },
    onError: (error) => logMutationError('updateGoAgentTier', error),
  });
}

/**
 * Hook for triggering a drift check on a Go agent
 */
export function useTriggerGoAgentCheck() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, agentId }: { siteId: string; agentId: string }) =>
      goAgentsApi.triggerCheck(siteId, agentId),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['goAgents', siteId] });
    },
    onError: (error) => logMutationError('triggerGoAgentCheck', error),
  });
}

/**
 * Hook for removing a Go agent from registry
 */
export function useRemoveGoAgent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ siteId, agentId }: { siteId: string; agentId: string }) =>
      goAgentsApi.removeAgent(siteId, agentId),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['goAgents', siteId] });
    },
    onError: (error) => logMutationError('removeGoAgent', error),
  });
}

// =============================================================================
// DEVICE INVENTORY HOOKS (Network scanner discovered devices)
// =============================================================================

/**
 * Hook for fetching discovered devices for a site
 */
export function useSiteDevices(
  siteId: string | null,
  params?: {
    device_type?: string;
    compliance_status?: string;
    include_medical?: boolean;
    limit?: number;
    offset?: number;
  }
) {
  const defaults = useQueryDefaults();
  return useQuery<SiteDevicesResponse>({
    queryKey: ['devices', siteId, params],
    queryFn: () => devicesApi.getSiteDevices(siteId!, params),
    enabled: !!siteId,
    ...defaults,
  });
}

/**
 * Hook for fetching device summary for a site
 */
export function useSiteDeviceSummary(siteId: string | null) {
  const defaults = useQueryDefaults();
  return useQuery<SiteDeviceSummary>({
    queryKey: ['devices', siteId, 'summary'],
    queryFn: () => devicesApi.getSiteSummary(siteId!),
    enabled: !!siteId,
    ...defaults,
  });
}

/**
 * Hook for fetching medical devices for a site
 */
export function useSiteMedicalDevices(siteId: string | null, limit?: number, offset?: number) {
  const defaults = useQueryDefaults();
  return useQuery<{
    site_id: string;
    medical_devices: DiscoveredDevice[];
    total: number;
    note: string;
  }>({
    queryKey: ['devices', siteId, 'medical', limit, offset],
    queryFn: () => devicesApi.getMedicalDevices(siteId!, limit, offset),
    enabled: !!siteId,
    ...defaults,
  });
}

// =============================================================================
// CVE Watch Hooks
// =============================================================================

export function useCVESummary() {
  const defaults = useQueryDefaults();
  return useQuery<CVESummary>({
    queryKey: ['cve-summary'],
    queryFn: cveApi.getSummary,
    ...defaults,
  });
}

export function useCVEs(params?: { severity?: string; status?: string; search?: string }) {
  const defaults = useQueryDefaults();
  return useQuery<CVEEntry[]>({
    queryKey: ['cves', params],
    queryFn: () => cveApi.getCVEs(params),
    ...defaults,
  });
}

export function useCVEDetail(cveId: string | null) {
  const defaults = useQueryDefaults();
  return useQuery<CVEDetail>({
    queryKey: ['cve', cveId],
    queryFn: () => cveApi.getCVE(cveId!),
    enabled: !!cveId,
    ...defaults,
  });
}

export function useTriggerCVESync() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: cveApi.triggerSync,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cves'] });
      queryClient.invalidateQueries({ queryKey: ['cve-summary'] });
    },
  });
}

export function useUpdateCVEStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ cveId, status, notes }: { cveId: string; status: string; notes?: string }) =>
      cveApi.updateStatus(cveId, status, notes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cves'] });
      queryClient.invalidateQueries({ queryKey: ['cve-summary'] });
      queryClient.invalidateQueries({ queryKey: ['cve'] });
    },
  });
}

export function useCVEWatchConfig() {
  const defaults = useQueryDefaults();
  return useQuery<CVEWatchConfig>({
    queryKey: ['cve-config'],
    queryFn: cveApi.getConfig,
    ...defaults,
  });
}

// =============================================================================
// Framework Sync Hooks (Compliance Library)
// =============================================================================

export function useFrameworkSyncStatus() {
  const defaults = useQueryDefaults();
  return useQuery<FrameworkSyncStatus[]>({
    queryKey: ['framework-sync-status'],
    queryFn: frameworkSyncApi.getStatus,
    ...defaults,
  });
}

export function useFrameworkControls(framework: string | null, params?: { category?: string; search?: string }) {
  const defaults = useQueryDefaults();
  return useQuery<FrameworkControl[]>({
    queryKey: ['framework-controls', framework, params],
    queryFn: () => frameworkSyncApi.getControls(framework!, params),
    enabled: !!framework,
    ...defaults,
  });
}

export function useFrameworkCategories(framework: string | null) {
  const defaults = useQueryDefaults();
  return useQuery<FrameworkCategory[]>({
    queryKey: ['framework-categories', framework],
    queryFn: () => frameworkSyncApi.getCategories(framework!),
    enabled: !!framework,
    ...defaults,
  });
}

export function useCoverageAnalysis() {
  const defaults = useQueryDefaults();
  return useQuery<CoverageAnalysis>({
    queryKey: ['framework-coverage'],
    queryFn: frameworkSyncApi.getCoverage,
    ...defaults,
  });
}

export function useTriggerFrameworkSync() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: frameworkSyncApi.triggerSync,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['framework-sync-status'] });
      queryClient.invalidateQueries({ queryKey: ['framework-controls'] });
      queryClient.invalidateQueries({ queryKey: ['framework-coverage'] });
    },
  });
}

export function useSyncFramework() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (framework: string) => frameworkSyncApi.syncFramework(framework),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['framework-sync-status'] });
      queryClient.invalidateQueries({ queryKey: ['framework-controls'] });
      queryClient.invalidateQueries({ queryKey: ['framework-coverage'] });
    },
  });
}

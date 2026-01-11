/**
 * React hooks for fleet data fetching
 */

import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { fleetApi, incidentApi, statsApi, learningApi, runbookApi, onboardingApi, sitesApi, ordersApi, notificationsApi, runbookConfigApi } from '../utils/api';
import type { Site, SiteDetail, OrderType, OrderResponse, RunbookCatalogItem, SiteRunbookConfig } from '../utils/api';
import type { ClientOverview, ClientDetail, Incident, ComplianceEvent, GlobalStats, LearningStatus, PromotionCandidate, PromotionHistory, Runbook, RunbookDetail, RunbookExecution, OnboardingClient, OnboardingMetrics, Notification, NotificationSummary } from '../types';

// Polling interval in milliseconds (60 seconds - reduced from 30s to prevent flickering)
const POLLING_INTERVAL = 60_000;
const STALE_TIME = 30_000; // Consider data fresh for 30 seconds

/**
 * Hook for fetching fleet overview data with polling
 */
export function useFleet() {
  return useQuery<ClientOverview[]>({
    queryKey: ['fleet'],
    queryFn: fleetApi.getFleet,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching a single client's details
 */
export function useClient(siteId: string | null) {
  return useQuery<ClientDetail>({
    queryKey: ['client', siteId],
    queryFn: () => fleetApi.getClient(siteId!),
    enabled: !!siteId,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
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
  return useQuery<Incident[]>({
    queryKey: ['incidents', params],
    queryFn: () => incidentApi.getIncidents(params),
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching compliance events (drift detections from compliance bundles)
 */
export function useEvents(params?: {
  site_id?: string;
  limit?: number;
}) {
  return useQuery<ComplianceEvent[]>({
    queryKey: ['events', params],
    queryFn: () => incidentApi.getEvents(params),
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching global stats
 */
export function useGlobalStats() {
  return useQuery<GlobalStats>({
    queryKey: ['stats'],
    queryFn: statsApi.getGlobalStats,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching learning loop status
 */
export function useLearningStatus() {
  return useQuery<LearningStatus>({
    queryKey: ['learning', 'status'],
    queryFn: learningApi.getStatus,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook to manually trigger a refresh of fleet data
 */
export function useRefreshFleet() {
  const queryClient = useQueryClient();

  return () => {
    queryClient.invalidateQueries({ queryKey: ['fleet'] });
    queryClient.invalidateQueries({ queryKey: ['incidents'] });
    queryClient.invalidateQueries({ queryKey: ['stats'] });
    queryClient.invalidateQueries({ queryKey: ['learning'] });
    queryClient.invalidateQueries({ queryKey: ['runbooks'] });
  };
}

/**
 * Hook for fetching all runbooks
 */
export function useRunbooks() {
  return useQuery<Runbook[]>({
    queryKey: ['runbooks'],
    queryFn: runbookApi.getRunbooks,
    staleTime: STALE_TIME,
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
  });
}

/**
 * Hook for fetching promotion candidates
 */
export function usePromotionCandidates() {
  return useQuery<PromotionCandidate[]>({
    queryKey: ['learning', 'candidates'],
    queryFn: learningApi.getCandidates,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching promotion history
 */
export function usePromotionHistory(limit?: number) {
  return useQuery<PromotionHistory[]>({
    queryKey: ['learning', 'history', limit],
    queryFn: () => learningApi.getHistory(limit),
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
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
      // Invalidate learning queries to refetch updated data
      queryClient.invalidateQueries({ queryKey: ['learning'] });
    },
  });
}

/**
 * Hook for fetching onboarding pipeline
 */
export function useOnboardingPipeline() {
  return useQuery<OnboardingClient[]>({
    queryKey: ['onboarding', 'pipeline'],
    queryFn: onboardingApi.getPipeline,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching onboarding metrics
 */
export function useOnboardingMetrics() {
  return useQuery<OnboardingMetrics>({
    queryKey: ['onboarding', 'metrics'],
    queryFn: onboardingApi.getMetrics,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

// =============================================================================
// SITES HOOKS (Real appliance onboarding data)
// =============================================================================

/**
 * Hook for fetching all sites with live status
 */
export function useSites(status?: string) {
  return useQuery<{ sites: Site[]; count: number }>({
    queryKey: ['sites', status],
    queryFn: () => sitesApi.getSites(status),
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching a single site's details
 */
export function useSite(siteId: string | null) {
  return useQuery<SiteDetail>({
    queryKey: ['site', siteId],
    queryFn: () => sitesApi.getSite(siteId!),
    enabled: !!siteId,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
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
  });
}

// =============================================================================
// ORDER HOOKS
// =============================================================================

/**
 * Hook for fetching orders for a site
 */
export function useOrders(siteId: string | null, status?: string) {
  return useQuery<OrderResponse[]>({
    queryKey: ['orders', siteId, status],
    queryFn: () => ordersApi.getOrders(siteId!, status as any),
    enabled: !!siteId,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
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
  return useQuery<Notification[]>({
    queryKey: ['notifications', params],
    queryFn: () => notificationsApi.getNotifications(params),
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching notification summary (counts)
 */
export function useNotificationSummary() {
  return useQuery<NotificationSummary>({
    queryKey: ['notifications', 'summary'],
    queryFn: notificationsApi.getSummary,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
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
  });
}

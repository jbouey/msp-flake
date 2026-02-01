/**
 * React hooks for Cloud Integrations
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  integrationsApi,
  Integration,
  IntegrationCreateRequest,
  IntegrationCreateResponse,
  SiteIntegrationsHealth,
  ResourcesResponse,
  SyncJob,
  AWSSetupInstructions,
} from '../utils/integrationsApi';

// Polling intervals
const POLLING_INTERVAL = 60_000; // 60 seconds
const SYNC_POLLING_INTERVAL = 5_000; // 5 seconds for active sync jobs
const STALE_TIME = 30_000;

/**
 * Helper to log mutation errors consistently
 */
function logMutationError(context: string, error: unknown): void {
  // Skip logging for aborted requests
  if (error instanceof Error && error.message.includes('cancelled')) {
    return;
  }
  console.error(`Integration mutation error (${context}):`, error);
}

/**
 * Hook for fetching all integrations for a site
 */
export function useIntegrations(
  siteId: string | null,
  params?: { status?: string; provider?: string }
) {
  return useQuery<Integration[]>({
    queryKey: ['integrations', siteId, params],
    queryFn: () => integrationsApi.listIntegrations(siteId!, params),
    enabled: !!siteId,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching a single integration
 */
export function useIntegration(siteId: string | null, integrationId: string | null) {
  return useQuery<Integration>({
    queryKey: ['integration', siteId, integrationId],
    queryFn: () => integrationsApi.getIntegration(siteId!, integrationId!),
    enabled: !!siteId && !!integrationId,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for creating a new integration
 */
export function useCreateIntegration() {
  const queryClient = useQueryClient();

  return useMutation<IntegrationCreateResponse, Error, { siteId: string; data: IntegrationCreateRequest }>({
    mutationFn: ({ siteId, data }) => integrationsApi.createIntegration(siteId, data),
    onSuccess: (_, { siteId }) => {
      queryClient.invalidateQueries({ queryKey: ['integrations', siteId] });
      queryClient.invalidateQueries({ queryKey: ['integrationsHealth', siteId] });
    },
    onError: (error) => logMutationError('createIntegration', error),
  });
}

/**
 * Hook for deleting an integration
 */
export function useDeleteIntegration() {
  const queryClient = useQueryClient();

  return useMutation<{ message: string }, Error, { siteId: string; integrationId: string }>({
    mutationFn: ({ siteId, integrationId }) =>
      integrationsApi.deleteIntegration(siteId, integrationId),
    onSuccess: (_, { siteId, integrationId }) => {
      queryClient.invalidateQueries({ queryKey: ['integrations', siteId] });
      queryClient.invalidateQueries({ queryKey: ['integration', siteId, integrationId] });
      queryClient.invalidateQueries({ queryKey: ['integrationsHealth', siteId] });
    },
    onError: (error) => logMutationError('deleteIntegration', error),
  });
}

/**
 * Hook for fetching resources from an integration
 */
export function useIntegrationResources(
  siteId: string | null,
  integrationId: string | null,
  params?: { resource_type?: string; risk_level?: string; limit?: number; offset?: number }
) {
  return useQuery<ResourcesResponse>({
    queryKey: ['integrationResources', siteId, integrationId, params],
    queryFn: () => integrationsApi.listResources(siteId!, integrationId!, params),
    enabled: !!siteId && !!integrationId,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for triggering a manual sync
 */
export function useTriggerSync() {
  const queryClient = useQueryClient();

  return useMutation<
    { job_id: string; status: string; message: string },
    Error,
    { siteId: string; integrationId: string }
  >({
    mutationFn: ({ siteId, integrationId }) =>
      integrationsApi.triggerSync(siteId, integrationId),
    onSuccess: (_, { siteId, integrationId }) => {
      queryClient.invalidateQueries({ queryKey: ['integration', siteId, integrationId] });
    },
    onError: (error) => logMutationError('triggerSync', error),
  });
}

/**
 * Hook for fetching sync job status with frequent polling
 */
export function useSyncJob(
  siteId: string | null,
  integrationId: string | null,
  jobId: string | null
) {
  return useQuery<SyncJob>({
    queryKey: ['syncJob', siteId, integrationId, jobId],
    queryFn: () => integrationsApi.getSyncStatus(siteId!, integrationId!, jobId!),
    enabled: !!siteId && !!integrationId && !!jobId,
    refetchInterval: (query) => {
      // Poll more frequently while sync is running
      if (query.state.data?.status === 'running' || query.state.data?.status === 'pending') {
        return SYNC_POLLING_INTERVAL;
      }
      return false; // Stop polling when complete
    },
    staleTime: 1000, // Very short stale time for sync jobs
  });
}

/**
 * Hook for fetching site integrations health overview
 */
export function useIntegrationsHealth(siteId: string | null) {
  return useQuery<SiteIntegrationsHealth>({
    queryKey: ['integrationsHealth', siteId],
    queryFn: () => integrationsApi.getSiteHealth(siteId!),
    enabled: !!siteId,
    refetchInterval: POLLING_INTERVAL,
    staleTime: STALE_TIME,
  });
}

/**
 * Hook for fetching AWS setup instructions
 */
export function useAWSSetupInstructions() {
  return useQuery<AWSSetupInstructions>({
    queryKey: ['awsSetupInstructions'],
    queryFn: integrationsApi.getAWSSetupInstructions,
    staleTime: 24 * 60 * 60 * 1000, // Cache for 24 hours
  });
}

/**
 * Hook for generating AWS external ID
 */
export function useGenerateAWSExternalId() {
  return useMutation<{ external_id: string }, Error, void>({
    mutationFn: () => integrationsApi.generateAWSExternalId(),
    onError: (error) => logMutationError('generateAWSExternalId', error),
  });
}

/**
 * Hook to manually refresh integration data
 */
export function useRefreshIntegrations() {
  const queryClient = useQueryClient();

  return (siteId: string) => {
    queryClient.invalidateQueries({ queryKey: ['integrations', siteId] });
    queryClient.invalidateQueries({ queryKey: ['integrationsHealth', siteId] });
  };
}

/**
 * Hook to refresh resources after sync
 */
export function useRefreshResources() {
  const queryClient = useQueryClient();

  return (siteId: string, integrationId: string) => {
    queryClient.invalidateQueries({ queryKey: ['integrationResources', siteId, integrationId] });
    queryClient.invalidateQueries({ queryKey: ['integration', siteId, integrationId] });
  };
}

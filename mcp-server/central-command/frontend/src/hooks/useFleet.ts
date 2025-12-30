/**
 * React hooks for fleet data fetching
 */

import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { fleetApi, incidentApi, statsApi, learningApi, runbookApi, onboardingApi } from '../utils/api';
import type { ClientOverview, ClientDetail, Incident, GlobalStats, LearningStatus, PromotionCandidate, PromotionHistory, Runbook, RunbookDetail, RunbookExecution, OnboardingClient, OnboardingMetrics } from '../types';

// Polling interval in milliseconds (30 seconds)
const POLLING_INTERVAL = 30_000;
const STALE_TIME = 10_000; // Consider data fresh for 10 seconds

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

/**
 * React hooks for zero-friction deployment status
 */

import { useQuery } from '@tanstack/react-query';
import { deploymentApi } from '../utils/api';
import type { DeploymentStatus } from '../types';

// Polling interval for deployment status (5 seconds - active deployment needs frequent updates)
const POLLING_INTERVAL = 5_000;
const STALE_TIME = 2_000; // Consider data fresh for 2 seconds

/**
 * Hook for fetching deployment status with frequent polling
 */
export function useDeploymentStatus(siteId: string | null) {
  return useQuery<DeploymentStatus>({
    queryKey: ['deployment', siteId],
    queryFn: () => deploymentApi.getStatus(siteId!),
    enabled: !!siteId,
    refetchInterval: (query) => {
      // Poll more frequently while deployment is in progress
      const phase = query.state.data?.phase;
      if (phase && phase !== 'complete') {
        return POLLING_INTERVAL;
      }
      return false; // Stop polling when complete
    },
    staleTime: STALE_TIME,
  });
}

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Badge } from '../../../components/shared';
import { formatTimeAgo } from '../../../constants';

const formatRelativeTime = formatTimeAgo;

export interface EvidenceChainStatusProps {
  siteId: string;
}

/**
 * Evidence chain signing status for partner visibility
 */
export const EvidenceChainStatus: React.FC<EvidenceChainStatusProps> = ({ siteId }) => {
  const { data, isLoading } = useQuery<{
    status: string;
    has_key: boolean;
    key_fingerprint: string | null;
    evidence_rejection_count: number;
    last_rejection: string | null;
    last_accepted: string | null;
    verified_bundle_count: number;
    last_evidence: string | null;
  }>({
    queryKey: ['evidence-signing-status', siteId],
    queryFn: async () => {
      const res = await fetch(`/api/evidence/sites/${siteId}/signing-status`, {
        credentials: 'include',
      });
      if (!res.ok) return null;
      return res.json();
    },
    staleTime: 30_000,
    retry: false,
  });

  if (isLoading || !data) return null;

  const statusColor = data.status === 'healthy' ? 'success' : data.status === 'broken' ? 'error' : 'default';
  const statusLabel = data.status === 'healthy' ? 'Active' : data.status === 'broken' ? 'Broken' : 'No Key';

  return (
    <GlassCard>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Evidence Chain</h2>
        <Badge variant={statusColor}>{statusLabel}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-label-tertiary">Signing Key</p>
          <p className="text-label-primary font-mono text-xs">
            {data.key_fingerprint || 'Not registered'}
          </p>
        </div>
        <div>
          <p className="text-label-tertiary">Verified Bundles</p>
          <p className="text-label-primary">{data.verified_bundle_count}</p>
        </div>
        <div>
          <p className="text-label-tertiary">Last Accepted</p>
          <p className="text-label-primary">{data.last_accepted ? formatRelativeTime(data.last_accepted) : 'Never'}</p>
        </div>
        {data.evidence_rejection_count > 0 && (
          <div>
            <p className="text-label-tertiary text-red-500">Rejections</p>
            <p className="text-red-500 font-semibold">
              {data.evidence_rejection_count} ({data.last_rejection ? formatRelativeTime(data.last_rejection) : ''})
            </p>
          </div>
        )}
      </div>
    </GlassCard>
  );
};

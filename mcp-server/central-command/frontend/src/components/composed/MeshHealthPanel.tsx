/**
 * MeshHealthPanel — visualizes mesh state for multi-appliance sites.
 *
 * Shows ring state, per-appliance target assignments, coverage gaps,
 * and recent assignment changes. Queries /api/sites/{site_id}/mesh.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { sitesApi } from '../../utils/api';

interface MeshAppliance {
  appliance_id: string;
  display_name: string | null;
  hostname: string | null;
  mac_address: string | null;
  online: boolean;
  ring_size: number;
  peer_count: number;
  target_count: number;
  assigned_targets: string[];
  assignment_epoch: number | null;
  last_checkin: string | null;
}

interface MeshState {
  site_id: string;
  mesh_active: boolean;
  appliances: MeshAppliance[];
  summary: {
    total_appliances: number;
    online_count: number;
    ring_agreement: boolean;
    ring_drift: boolean;
    unique_targets: number;
    total_assignments: number;
    overlap_count: number;
    overlap_samples: string[];
    health_status: 'healthy' | 'degraded' | 'critical';
    health_issues: string[];
  };
  audit_history: Array<{
    appliance_id: string;
    assignment_epoch: number;
    ring_size: number;
    target_count: number;
    changed_at: string;
  }>;
}

interface Props {
  siteId: string;
}

const healthBadge: Record<string, string> = {
  healthy: 'bg-health-healthy text-white',
  degraded: 'bg-amber-500 text-white',
  critical: 'bg-health-critical text-white',
};

export const MeshHealthPanel: React.FC<Props> = ({ siteId }) => {
  const { data, isLoading, error } = useQuery<MeshState>({
    queryKey: ['mesh', siteId],
    queryFn: () => sitesApi.getMeshState(siteId) as unknown as Promise<MeshState>,
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <div className="rounded-xl border border-glass-border bg-background-secondary p-6">
        <div className="text-label-tertiary">Loading mesh state…</div>
      </div>
    );
  }

  if (error || !data) {
    return null; // Fail silently — mesh panel is optional
  }

  if (!data.mesh_active) {
    return null; // Single-appliance site, no mesh to show
  }

  const { summary, appliances, audit_history } = data;

  return (
    <div className="rounded-xl border border-glass-border bg-background-secondary p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-label-primary">Mesh Health</h3>
        <span className={`px-3 py-1 rounded-full text-xs font-medium ${healthBadge[summary.health_status]}`}>
          {summary.health_status === 'healthy' ? 'Healthy' : summary.health_status === 'degraded' ? 'Degraded' : 'Critical'}
        </span>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div>
          <div className="text-xs text-label-tertiary uppercase">Appliances</div>
          <div className="text-xl font-semibold text-label-primary">
            {summary.online_count}/{summary.total_appliances}
          </div>
          <div className="text-xs text-label-tertiary">online</div>
        </div>
        <div>
          <div className="text-xs text-label-tertiary uppercase">Ring Agreement</div>
          <div className={`text-xl font-semibold ${summary.ring_agreement ? 'text-health-healthy' : 'text-health-critical'}`}>
            {summary.ring_agreement ? 'Yes' : 'No'}
          </div>
          <div className="text-xs text-label-tertiary">
            {summary.ring_drift ? 'ring drift detected' : 'all agree'}
          </div>
        </div>
        <div>
          <div className="text-xs text-label-tertiary uppercase">Unique Targets</div>
          <div className="text-xl font-semibold text-label-primary">{summary.unique_targets}</div>
          <div className="text-xs text-label-tertiary">
            {summary.total_assignments} assignments
          </div>
        </div>
        <div>
          <div className="text-xs text-label-tertiary uppercase">Overlaps</div>
          <div className={`text-xl font-semibold ${summary.overlap_count === 0 ? 'text-health-healthy' : 'text-amber-500'}`}>
            {summary.overlap_count}
          </div>
          <div className="text-xs text-label-tertiary">
            {summary.overlap_count === 0 ? 'no duplicate scans' : 'duplicate scans'}
          </div>
        </div>
      </div>

      {/* Health issues */}
      {summary.health_issues.length > 0 && (
        <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30">
          <div className="text-xs font-medium text-amber-400 uppercase mb-1">Issues</div>
          <ul className="text-sm text-label-primary space-y-1">
            {summary.health_issues.map((issue, i) => (
              <li key={i}>• {issue}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Per-appliance breakdown */}
      <div className="mb-4">
        <div className="text-xs text-label-tertiary uppercase mb-2">Ring Members</div>
        <div className="space-y-2">
          {appliances.map((a) => (
            <div
              key={a.appliance_id}
              className="flex items-center justify-between p-3 rounded-lg bg-glass-bg border border-glass-border"
            >
              <div className="flex items-center gap-3">
                <div className={`w-2 h-2 rounded-full ${a.online ? 'bg-health-healthy' : 'bg-slate-500'}`} />
                <div>
                  <div className="text-sm font-medium text-label-primary">
                    {a.display_name || a.hostname || 'Unknown'}
                  </div>
                  <div className="text-xs text-label-tertiary font-mono">
                    ring={a.ring_size} peers={a.peer_count} targets={a.target_count}
                  </div>
                </div>
              </div>
              <div className="text-xs text-label-tertiary">
                {a.online ? 'online' : 'offline'}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Recent changes */}
      {audit_history.length > 0 && (
        <div>
          <div className="text-xs text-label-tertiary uppercase mb-2">Recent Assignment Changes</div>
          <div className="space-y-1 text-xs">
            {audit_history.slice(0, 5).map((h, i) => (
              <div key={i} className="flex justify-between text-label-secondary">
                <span className="font-mono">{h.appliance_id.slice(0, 32)}</span>
                <span>{h.target_count} targets • {new Date(h.changed_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

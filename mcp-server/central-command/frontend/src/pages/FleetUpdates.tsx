import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { GlassCard } from '../components/shared';
import { fleetApi, type FleetRelease, type FleetRollout, type FleetStats } from '../utils/api';

const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const colors: Record<string, string> = {
    in_progress: 'bg-accent-primary text-white',
    paused: 'bg-health-warning text-white',
    completed: 'bg-health-healthy text-white',
    failed: 'bg-health-critical text-white',
    cancelled: 'bg-fill-tertiary text-label-secondary',
    pending: 'bg-fill-secondary text-label-primary',
  };

  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${colors[status] || colors.pending}`}>
      {status.replace('_', ' ')}
    </span>
  );
};

const ProgressBar: React.FC<{ progress: FleetRollout['progress'] }> = ({ progress }) => {
  if (!progress) return null;

  const total = progress.total || 1;
  const successWidth = (progress.succeeded / total) * 100;
  const failedWidth = (progress.failed / total) * 100;
  const inProgressWidth = (progress.in_progress / total) * 100;

  return (
    <div className="w-full">
      <div className="flex h-2 rounded-full overflow-hidden bg-fill-tertiary">
        <div className="bg-health-healthy" style={{ width: `${successWidth}%` }} />
        <div className="bg-accent-primary" style={{ width: `${inProgressWidth}%` }} />
        <div className="bg-health-critical" style={{ width: `${failedWidth}%` }} />
      </div>
      <div className="flex justify-between text-xs text-label-tertiary mt-1">
        <span>{progress.succeeded} succeeded</span>
        <span>{progress.pending} pending</span>
        <span>{progress.failed} failed</span>
      </div>
    </div>
  );
};

export const FleetUpdates: React.FC = () => {
  const queryClient = useQueryClient();
  const [showCreateRelease, setShowCreateRelease] = useState(false);
  const [showCreateRollout, setShowCreateRollout] = useState(false);
  const [selectedReleaseId, setSelectedReleaseId] = useState<string | null>(null);
  const [newRelease, setNewRelease] = useState({
    version: '',
    iso_url: '',
    sha256: '',
    release_notes: '',
    agent_version: '',
  });

  // Fetch fleet stats
  const { data: stats } = useQuery<FleetStats>({
    queryKey: ['fleet-stats'],
    queryFn: () => fleetApi.getStats(),
  });

  // Fetch releases
  const { data: releases = [], isLoading: releasesLoading } = useQuery<FleetRelease[]>({
    queryKey: ['fleet-releases'],
    queryFn: () => fleetApi.getReleases(),
  });

  // Fetch rollouts
  const { data: rollouts = [], isLoading: rolloutsLoading } = useQuery<FleetRollout[]>({
    queryKey: ['fleet-rollouts'],
    queryFn: () => fleetApi.getRollouts(),
  });

  // Create release mutation
  const createReleaseMutation = useMutation({
    mutationFn: (release: typeof newRelease) => fleetApi.createRelease(release),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fleet-releases'] });
      queryClient.invalidateQueries({ queryKey: ['fleet-stats'] });
      setShowCreateRelease(false);
      setNewRelease({ version: '', iso_url: '', sha256: '', release_notes: '', agent_version: '' });
    },
  });

  // Create rollout mutation
  const createRolloutMutation = useMutation({
    mutationFn: (releaseId: string) => fleetApi.createRollout({
      release_id: releaseId,
      strategy: 'staged',
      stages: [
        { percent: 5, delay_hours: 24 },
        { percent: 25, delay_hours: 24 },
        { percent: 100, delay_hours: 0 },
      ],
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fleet-rollouts'] });
      queryClient.invalidateQueries({ queryKey: ['fleet-stats'] });
      setShowCreateRollout(false);
      setSelectedReleaseId(null);
    },
  });

  // Rollout control mutations
  const pauseRolloutMutation = useMutation({
    mutationFn: (rolloutId: string) => fleetApi.pauseRollout(rolloutId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fleet-rollouts'] }),
  });

  const resumeRolloutMutation = useMutation({
    mutationFn: (rolloutId: string) => fleetApi.resumeRollout(rolloutId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fleet-rollouts'] }),
  });

  const advanceRolloutMutation = useMutation({
    mutationFn: (rolloutId: string) => fleetApi.advanceRollout(rolloutId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fleet-rollouts'] }),
  });

  const setLatestMutation = useMutation({
    mutationFn: (version: string) => fleetApi.setLatest(version),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fleet-releases'] });
      queryClient.invalidateQueries({ queryKey: ['fleet-stats'] });
    },
  });

  return (
    <div className="space-y-6">
      {/* Stats Overview */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <GlassCard className="p-4">
          <div className="text-sm text-label-tertiary">Latest Version</div>
          <div className="text-2xl font-bold text-accent-primary mt-1">
            {stats?.releases.latest_version || 'None'}
          </div>
        </GlassCard>
        <GlassCard className="p-4">
          <div className="text-sm text-label-tertiary">Active Rollouts</div>
          <div className="text-2xl font-bold text-health-warning mt-1">
            {stats?.rollouts.in_progress || 0}
          </div>
        </GlassCard>
        <GlassCard className="p-4">
          <div className="text-sm text-label-tertiary">30-Day Success Rate</div>
          <div className="text-2xl font-bold text-health-healthy mt-1">
            {stats?.appliance_updates_30d.success_rate || 0}%
          </div>
        </GlassCard>
        <GlassCard className="p-4">
          <div className="text-sm text-label-tertiary">Updates (30d)</div>
          <div className="text-2xl font-bold text-label-primary mt-1">
            {stats?.appliance_updates_30d.total || 0}
          </div>
        </GlassCard>
      </div>

      {/* Active Rollouts */}
      <GlassCard>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-label-primary">Active Rollouts</h2>
        </div>

        {rolloutsLoading && (
          <div className="text-center py-8">
            <div className="w-8 h-8 border-2 border-accent-primary border-t-transparent rounded-full animate-spin mx-auto" />
          </div>
        )}

        {!rolloutsLoading && rollouts.filter(r => r.status === 'in_progress' || r.status === 'paused').length === 0 && (
          <div className="text-center py-8 text-label-tertiary">
            No active rollouts
          </div>
        )}

        {!rolloutsLoading && rollouts.filter(r => r.status === 'in_progress' || r.status === 'paused').map((rollout) => (
          <div key={rollout.id} className="border border-separator rounded-ios-lg p-4 mb-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <span className="font-medium text-label-primary">{rollout.name || rollout.version}</span>
                <span className="ml-2"><StatusBadge status={rollout.status} /></span>
              </div>
              <div className="flex gap-2">
                {rollout.status === 'in_progress' && (
                  <>
                    <button
                      onClick={() => pauseRolloutMutation.mutate(rollout.id)}
                      className="px-3 py-1 text-sm bg-health-warning text-white rounded-ios-md hover:opacity-90"
                    >
                      Pause
                    </button>
                    <button
                      onClick={() => advanceRolloutMutation.mutate(rollout.id)}
                      className="px-3 py-1 text-sm bg-accent-primary text-white rounded-ios-md hover:opacity-90"
                      disabled={rollout.current_stage >= rollout.stages.length - 1}
                    >
                      Advance Stage
                    </button>
                  </>
                )}
                {rollout.status === 'paused' && (
                  <button
                    onClick={() => resumeRolloutMutation.mutate(rollout.id)}
                    className="px-3 py-1 text-sm bg-health-healthy text-white rounded-ios-md hover:opacity-90"
                  >
                    Resume
                  </button>
                )}
              </div>
            </div>

            <div className="text-sm text-label-secondary mb-2">
              Stage {rollout.current_stage + 1} of {rollout.stages.length} ({rollout.stages[rollout.current_stage]?.percent}%)
            </div>

            <ProgressBar progress={rollout.progress} />
          </div>
        ))}
      </GlassCard>

      {/* Releases */}
      <GlassCard>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-label-primary">Releases</h2>
          <button
            onClick={() => setShowCreateRelease(true)}
            className="px-4 py-2 bg-accent-primary text-white rounded-ios-md hover:opacity-90 flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            New Release
          </button>
        </div>

        {releasesLoading && (
          <div className="text-center py-8">
            <div className="w-8 h-8 border-2 border-accent-primary border-t-transparent rounded-full animate-spin mx-auto" />
          </div>
        )}

        {!releasesLoading && releases.length === 0 && (
          <div className="text-center py-8 text-label-tertiary">
            No releases yet. Create your first release to get started.
          </div>
        )}

        {!releasesLoading && releases.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left text-sm text-label-tertiary border-b border-separator">
                  <th className="pb-2 font-medium">Version</th>
                  <th className="pb-2 font-medium">Agent</th>
                  <th className="pb-2 font-medium">Size</th>
                  <th className="pb-2 font-medium">Created</th>
                  <th className="pb-2 font-medium">Status</th>
                  <th className="pb-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {releases.map((release) => (
                  <tr key={release.id} className="border-b border-separator last:border-0">
                    <td className="py-3">
                      <span className="font-medium text-label-primary">{release.version}</span>
                      {release.is_latest && (
                        <span className="ml-2 px-2 py-0.5 text-xs bg-health-healthy text-white rounded-full">
                          Latest
                        </span>
                      )}
                    </td>
                    <td className="py-3 text-label-secondary">{release.agent_version || '-'}</td>
                    <td className="py-3 text-label-secondary">
                      {release.size_bytes ? `${(release.size_bytes / 1024 / 1024 / 1024).toFixed(1)} GB` : '-'}
                    </td>
                    <td className="py-3 text-label-secondary">
                      {new Date(release.created_at).toLocaleDateString()}
                    </td>
                    <td className="py-3">
                      {release.is_active ? (
                        <span className="text-health-healthy">Active</span>
                      ) : (
                        <span className="text-label-tertiary">Inactive</span>
                      )}
                    </td>
                    <td className="py-3">
                      <div className="flex gap-2">
                        {!release.is_latest && release.is_active && (
                          <button
                            onClick={() => setLatestMutation.mutate(release.version)}
                            className="px-2 py-1 text-xs bg-fill-secondary text-label-primary rounded hover:bg-fill-tertiary"
                          >
                            Set Latest
                          </button>
                        )}
                        <button
                          onClick={() => {
                            setSelectedReleaseId(release.id);
                            setShowCreateRollout(true);
                          }}
                          className="px-2 py-1 text-xs bg-accent-primary text-white rounded hover:opacity-90"
                        >
                          Deploy
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {/* Recent Rollouts History */}
      <GlassCard>
        <h2 className="text-lg font-semibold text-label-primary mb-4">Rollout History</h2>

        {rollouts.filter(r => r.status !== 'in_progress' && r.status !== 'paused').slice(0, 10).map((rollout) => (
          <div key={rollout.id} className="flex items-center justify-between py-3 border-b border-separator last:border-0">
            <div>
              <span className="font-medium text-label-primary">{rollout.name || rollout.version}</span>
              <span className="ml-2"><StatusBadge status={rollout.status} /></span>
              <div className="text-sm text-label-tertiary">
                {rollout.completed_at ? new Date(rollout.completed_at).toLocaleString() : '-'}
              </div>
            </div>
            {rollout.progress && (
              <div className="text-right text-sm">
                <div className="text-health-healthy">{rollout.progress.succeeded} succeeded</div>
                {rollout.progress.failed > 0 && (
                  <div className="text-health-critical">{rollout.progress.failed} failed</div>
                )}
              </div>
            )}
          </div>
        ))}

        {rollouts.filter(r => r.status !== 'in_progress' && r.status !== 'paused').length === 0 && (
          <div className="text-center py-8 text-label-tertiary">
            No rollout history yet
          </div>
        )}
      </GlassCard>

      {/* Create Release Modal */}
      {showCreateRelease && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-bg-primary rounded-ios-xl p-6 w-full max-w-md">
            <h3 className="text-lg font-semibold text-label-primary mb-4">New Release</h3>

            <div className="space-y-4">
              <div>
                <label className="block text-sm text-label-secondary mb-1">Version</label>
                <input
                  type="text"
                  placeholder="v44"
                  value={newRelease.version}
                  onChange={(e) => setNewRelease({ ...newRelease, version: e.target.value })}
                  className="w-full px-3 py-2 rounded-ios-md bg-fill-secondary text-label-primary border border-separator focus:border-accent-primary focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-sm text-label-secondary mb-1">ISO URL</label>
                <input
                  type="text"
                  placeholder="https://updates.osiriscare.net/v44.iso"
                  value={newRelease.iso_url}
                  onChange={(e) => setNewRelease({ ...newRelease, iso_url: e.target.value })}
                  className="w-full px-3 py-2 rounded-ios-md bg-fill-secondary text-label-primary border border-separator focus:border-accent-primary focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-sm text-label-secondary mb-1">SHA256 Checksum</label>
                <input
                  type="text"
                  placeholder="abc123..."
                  value={newRelease.sha256}
                  onChange={(e) => setNewRelease({ ...newRelease, sha256: e.target.value })}
                  className="w-full px-3 py-2 rounded-ios-md bg-fill-secondary text-label-primary border border-separator focus:border-accent-primary focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-sm text-label-secondary mb-1">Agent Version</label>
                <input
                  type="text"
                  placeholder="1.0.44"
                  value={newRelease.agent_version}
                  onChange={(e) => setNewRelease({ ...newRelease, agent_version: e.target.value })}
                  className="w-full px-3 py-2 rounded-ios-md bg-fill-secondary text-label-primary border border-separator focus:border-accent-primary focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-sm text-label-secondary mb-1">Release Notes</label>
                <textarea
                  placeholder="What's new in this release..."
                  value={newRelease.release_notes}
                  onChange={(e) => setNewRelease({ ...newRelease, release_notes: e.target.value })}
                  className="w-full px-3 py-2 rounded-ios-md bg-fill-secondary text-label-primary border border-separator focus:border-accent-primary focus:outline-none h-24 resize-none"
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => setShowCreateRelease(false)}
                className="px-4 py-2 text-label-secondary hover:text-label-primary"
              >
                Cancel
              </button>
              <button
                onClick={() => createReleaseMutation.mutate(newRelease)}
                disabled={!newRelease.version || !newRelease.iso_url || !newRelease.sha256 || createReleaseMutation.isPending}
                className="px-4 py-2 bg-accent-primary text-white rounded-ios-md hover:opacity-90 disabled:opacity-50"
              >
                {createReleaseMutation.isPending ? 'Creating...' : 'Create Release'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Rollout Modal */}
      {showCreateRollout && selectedReleaseId && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-bg-primary rounded-ios-xl p-6 w-full max-w-md">
            <h3 className="text-lg font-semibold text-label-primary mb-4">Start Rollout</h3>

            <p className="text-label-secondary mb-4">
              This will start a staged rollout to all online appliances:
            </p>

            <div className="space-y-2 mb-6">
              <div className="flex justify-between text-sm">
                <span className="text-label-tertiary">Stage 1 (Canary)</span>
                <span className="text-label-primary">5% of fleet, 24h wait</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-label-tertiary">Stage 2</span>
                <span className="text-label-primary">25% of fleet, 24h wait</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-label-tertiary">Stage 3 (Full)</span>
                <span className="text-label-primary">100% of fleet</span>
              </div>
            </div>

            <div className="flex justify-end gap-3">
              <button
                onClick={() => {
                  setShowCreateRollout(false);
                  setSelectedReleaseId(null);
                }}
                className="px-4 py-2 text-label-secondary hover:text-label-primary"
              >
                Cancel
              </button>
              <button
                onClick={() => createRolloutMutation.mutate(selectedReleaseId)}
                disabled={createRolloutMutation.isPending}
                className="px-4 py-2 bg-accent-primary text-white rounded-ios-md hover:opacity-90 disabled:opacity-50"
              >
                {createRolloutMutation.isPending ? 'Starting...' : 'Start Rollout'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default FleetUpdates;

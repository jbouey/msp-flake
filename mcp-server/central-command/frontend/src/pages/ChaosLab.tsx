/**
 * Chaos Lab — admin push-button scenario activation.
 *
 * Session 205: previously the chaos lab fired only on cron. Now operators
 * can trigger any bundle on demand, useful for:
 *   - Reproducing conditions to debug L1/L2 matching
 *   - Validating flywheel promotion end-to-end
 *   - Sales demos ("watch our platform catch this")
 *
 * Talks to /api/admin/chaos/* which SSHes to the iMac via reverse tunnel.
 * Admin-only — no customer-facing exposure.
 */
import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { GlassCard, Spinner } from '../components/shared';
import { PageShell } from '../components/composed';
import {
  chaosLabApi,
  type ChaosBundle,
  type ChaosJobOutcome,
} from '../utils/api';

function difficultyColor(d: string | null): string {
  switch (d) {
    case 'easy': return 'text-health-healthy';
    case 'medium': return 'text-health-warning';
    case 'hard': return 'text-health-critical';
    default: return 'text-label-tertiary';
  }
}

function categoryColor(c: string | null): string {
  switch (c) {
    case 'malicious': return 'bg-red-100 text-red-700';
    case 'legitimate_drift': return 'bg-blue-100 text-blue-700';
    case 'operational_drift': return 'bg-amber-100 text-amber-700';
    default: return 'bg-gray-100 text-gray-600';
  }
}

const ChaosLab: React.FC = () => {
  const qc = useQueryClient();
  const [activeJob, setActiveJob] = useState<ChaosJobOutcome | null>(null);
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);

  const bundles = useQuery({
    queryKey: ['chaos-bundles'],
    queryFn: () => chaosLabApi.listBundles(),
    staleTime: 60_000,
  });

  const history = useQuery({
    queryKey: ['chaos-history'],
    queryFn: () => chaosLabApi.history(30),
    refetchInterval: 10_000,
  });

  const activateMutation = useMutation({
    mutationFn: (bundle_id: string) => chaosLabApi.activate(bundle_id),
    onSuccess: (data) => {
      setActiveJob(data);
      qc.invalidateQueries({ queryKey: ['chaos-history'] });
    },
  });

  const cleanupMutation = useMutation({
    mutationFn: (bundle_id: string) => chaosLabApi.cleanup(bundle_id),
    onSuccess: (data) => {
      setActiveJob(data);
      qc.invalidateQueries({ queryKey: ['chaos-history'] });
    },
  });

  return (
    <PageShell
      title="Chaos Lab"
      subtitle="Push-button chaos scenarios. Used to prove L1→L2→promotion paths work under realistic drift conditions. Admin-internal — not a customer feature."
    >
      {bundles.isError && (
        <GlassCard>
          <p className="text-health-warning text-sm p-4">
            Chaos lab API unavailable. Set CHAOS_LAB_ENABLED=true + SSH credentials
            in the Central Command environment, then restart the server.
          </p>
        </GlassCard>
      )}

      {bundles.isLoading && (
        <div className="flex items-center justify-center py-8">
          <Spinner />
        </div>
      )}

      {bundles.data && bundles.data.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
          {bundles.data.map((b: ChaosBundle) => (
            <GlassCard key={b.id}>
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="text-sm font-semibold text-label-primary">
                    {b.name}
                  </h3>
                  {b.difficulty && (
                    <span className={`text-xs font-semibold ${difficultyColor(b.difficulty)}`}>
                      {b.difficulty}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  {b.category && (
                    <span className={`text-[10px] px-2 py-0.5 rounded-full ${categoryColor(b.category)}`}>
                      {b.category.replace(/_/g, ' ')}
                    </span>
                  )}
                  {b.target && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                      → {b.target}
                    </span>
                  )}
                  {b.steps !== null && (
                    <span className="text-[10px] text-label-tertiary">
                      {b.steps} steps
                    </span>
                  )}
                </div>
                <p className="text-xs text-label-secondary font-mono truncate">
                  {b.id}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => activateMutation.mutate(b.id)}
                    disabled={activateMutation.isPending || cleanupMutation.isPending}
                    className="flex-1 px-3 py-2 text-xs font-medium rounded-ios text-white bg-ios-blue hover:bg-blue-600 disabled:opacity-50"
                  >
                    {activateMutation.isPending && activateMutation.variables === b.id
                      ? 'Running…'
                      : 'Activate'}
                  </button>
                  <button
                    onClick={() => cleanupMutation.mutate(b.id)}
                    disabled={activateMutation.isPending || cleanupMutation.isPending}
                    className="flex-1 px-3 py-2 text-xs font-medium rounded-ios text-label-primary bg-fill-secondary hover:bg-fill-tertiary disabled:opacity-50"
                    title="Undo this bundle's injections (for test-promotion cleanup)"
                  >
                    Cleanup
                  </button>
                </div>
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      {/* Latest job outcome */}
      {activeJob && (
        <GlassCard className="mb-6">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-label-primary">
                Latest Activation: {activeJob.bundle_id}
              </h3>
              <span className={`text-xs font-semibold ${
                activeJob.overall_success === true ? 'text-health-healthy' :
                activeJob.overall_success === false ? 'text-health-critical' :
                'text-health-warning'
              }`}>
                {activeJob.overall_success === true ? '✓ Success' :
                 activeJob.overall_success === false ? '✗ Failed' :
                 '⋯ Unknown'}
              </span>
            </div>
            <div className="text-xs text-label-tertiary">
              Job {activeJob.job_id} · {activeJob.duration_s?.toFixed(1)}s · rc={activeJob.returncode}
            </div>
            {activeJob.stdout && (
              <details className="mt-2">
                <summary className="text-xs text-ios-blue cursor-pointer">stdout</summary>
                <pre className="mt-2 p-2 bg-slate-50 rounded text-[10px] text-slate-700 whitespace-pre-wrap overflow-auto max-h-60">
                  {activeJob.stdout}
                </pre>
              </details>
            )}
            {activeJob.stderr && (
              <details className="mt-2">
                <summary className="text-xs text-health-critical cursor-pointer">stderr</summary>
                <pre className="mt-2 p-2 bg-red-50 rounded text-[10px] text-red-700 whitespace-pre-wrap overflow-auto max-h-60">
                  {activeJob.stderr}
                </pre>
              </details>
            )}
          </div>
        </GlassCard>
      )}

      {/* Recent history */}
      <GlassCard>
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-label-primary mb-2">
            Recent Activations
          </h3>
          {history.isLoading && <Spinner size="sm" />}
          {history.data && history.data.rows.length === 0 && (
            <p className="text-label-tertiary text-sm py-4">No bundles run yet.</p>
          )}
          {history.data && history.data.rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="min-w-full">
                <thead>
                  <tr className="text-left text-xs uppercase text-label-tertiary border-b border-separator-light">
                    <th className="py-2 pr-4">When</th>
                    <th className="py-2 pr-4">Bundle</th>
                    <th className="py-2 pr-4">Mode</th>
                    <th className="py-2 pr-4">Target</th>
                    <th className="py-2 pr-4">Result</th>
                    <th className="py-2 pr-4">Steps</th>
                    <th className="py-2 pr-4">Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {history.data.rows.map((r, i) => {
                    const id = r.job_id || `${r.timestamp}-${i}`;
                    const when = new Date(r.timestamp);
                    const ago = Math.round((Date.now() - when.getTime()) / 60_000);
                    const agoStr = ago < 60 ? `${ago}m` : ago < 1440 ? `${Math.round(ago / 60)}h` : `${Math.round(ago / 1440)}d`;
                    return (
                      <tr
                        key={id}
                        className="border-b border-separator-light cursor-pointer hover:bg-fill-primary/50"
                        onClick={() => setExpandedJobId(expandedJobId === id ? null : id)}
                      >
                        <td className="py-2 pr-4 text-xs tabular-nums">{agoStr} ago</td>
                        <td className="py-2 pr-4 text-sm font-mono">{r.bundle_id}</td>
                        <td className="py-2 pr-4 text-xs">{r.mode}</td>
                        <td className="py-2 pr-4 text-xs">{r.target_host}</td>
                        <td className={`py-2 pr-4 text-xs font-medium ${
                          r.overall_success ? 'text-health-healthy' : 'text-health-critical'
                        }`}>
                          {r.overall_success ? 'pass' : 'fail'}
                        </td>
                        <td className="py-2 pr-4 text-xs tabular-nums">
                          {r.steps_succeeded}/{r.step_count}
                        </td>
                        <td className="py-2 pr-4 text-xs tabular-nums">
                          {Number(r.duration_s).toFixed(1)}s
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </GlassCard>
    </PageShell>
  );
};

export default ChaosLab;

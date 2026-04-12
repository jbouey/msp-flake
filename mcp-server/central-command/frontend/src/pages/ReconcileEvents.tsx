/**
 * Reconcile Events — admin forensic timeline (Session 205 Phase 3).
 *
 * Shows every agent time-travel reconciliation: when an appliance woke
 * in a past state (VM snapshot revert, backup restore, disk clone),
 * what detection signals fired, what plan CC signed, and whether the
 * agent applied it successfully.
 *
 * Copy here avoids the words "time-travel" in user-facing text — calls
 * it "state reconciliation" per the Phase 2 round-table PM note.
 *
 * Admin-only — reveals cross-fleet crypto epoch rotations.
 */
import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Spinner } from '../components/shared';
import { PageShell } from '../components/composed';
import { reconcileApi, type ReconcileEvent } from '../utils/api';

function statusColor(status: string): string {
  switch (status) {
    case 'applied':
      return 'bg-green-100 text-green-800';
    case 'pending':
      return 'bg-amber-100 text-amber-800';
    case 'failed':
      return 'bg-red-100 text-red-800';
    case 'rejected':
      return 'bg-gray-100 text-gray-700';
    default:
      return 'bg-gray-100 text-gray-700';
  }
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString([], {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function shortHex(h: string | null, n = 8): string {
  if (!h) return '—';
  return h.length > n * 2 ? `${h.slice(0, n)}…${h.slice(-n)}` : h;
}

function signalBadge(signal: string): JSX.Element {
  return (
    <span
      key={signal}
      className="inline-flex items-center px-2 py-0.5 rounded text-xs font-mono bg-amber-50 text-amber-800 border border-amber-200"
    >
      {signal}
    </span>
  );
}

const EventRow: React.FC<{ event: ReconcileEvent; expanded: boolean; onToggle: () => void }> = ({
  event,
  expanded,
  onToggle,
}) => {
  return (
    <div className="border-b border-separator last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        className="w-full p-4 text-left hover:bg-fill-quaternary/50 transition-colors"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1">
              <span
                className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${statusColor(
                  event.plan_status,
                )}`}
              >
                {event.plan_status}
              </span>
              <span className="text-xs text-label-tertiary font-mono truncate">
                {event.appliance_id}
              </span>
            </div>
            <div className="text-sm text-label-primary">
              {formatDateTime(event.detected_at)} —{' '}
              <span className="text-label-secondary">
                {event.detection_signals.length} signal
                {event.detection_signals.length === 1 ? '' : 's'}
              </span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {event.detection_signals.map(signalBadge)}
            </div>
          </div>
          <div className="text-label-tertiary text-xs">{expanded ? '▲' : '▼'}</div>
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-2 bg-fill-quaternary/30 text-sm space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="text-xs text-label-tertiary uppercase tracking-wide mb-1">
                Site
              </div>
              <div className="font-mono text-label-secondary break-all">{event.site_id}</div>
            </div>
            <div>
              <div className="text-xs text-label-tertiary uppercase tracking-wide mb-1">
                Event ID
              </div>
              <div className="font-mono text-label-secondary break-all">{event.event_id}</div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <div className="text-xs text-label-tertiary uppercase tracking-wide mb-1">
                Reported boot counter
              </div>
              <div className="font-mono text-label-secondary">
                {event.reported_boot_counter ?? '—'}
              </div>
              <div className="text-xs text-label-tertiary mt-0.5">
                last known: {event.last_known_boot_counter ?? '—'}
              </div>
            </div>
            <div>
              <div className="text-xs text-label-tertiary uppercase tracking-wide mb-1">
                Reported uptime
              </div>
              <div className="font-mono text-label-secondary">
                {event.reported_uptime_seconds != null
                  ? `${event.reported_uptime_seconds}s`
                  : '—'}
              </div>
              <div className="text-xs text-label-tertiary mt-0.5">
                clock skew: {event.clock_skew_seconds ?? 0}s
              </div>
            </div>
            <div>
              <div className="text-xs text-label-tertiary uppercase tracking-wide mb-1">
                Generation UUID
              </div>
              <div className="font-mono text-label-secondary text-xs break-all">
                {event.reported_generation_uuid ?? '—'}
              </div>
              <div className="text-xs text-label-tertiary mt-0.5 break-all">
                last known: {event.last_known_generation_uuid ?? '—'}
              </div>
            </div>
          </div>

          {event.plan_runbook_ids && event.plan_runbook_ids.length > 0 && (
            <div>
              <div className="text-xs text-label-tertiary uppercase tracking-wide mb-1">
                Runbooks in plan
              </div>
              <div className="flex flex-wrap gap-1">
                {event.plan_runbook_ids.map((rb) => (
                  <span
                    key={rb}
                    className="inline-flex items-center px-2 py-0.5 rounded text-xs font-mono bg-blue-50 text-blue-800 border border-blue-200"
                  >
                    {rb}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="text-xs text-label-tertiary uppercase tracking-wide mb-1">
                Plan generated
              </div>
              <div className="text-label-secondary">{formatDateTime(event.plan_generated_at)}</div>
            </div>
            <div>
              <div className="text-xs text-label-tertiary uppercase tracking-wide mb-1">
                Plan applied
              </div>
              <div className="text-label-secondary">{formatDateTime(event.plan_applied_at)}</div>
            </div>
          </div>

          {event.plan_nonce_epoch_hex && (
            <div>
              <div className="text-xs text-label-tertiary uppercase tracking-wide mb-1">
                Nonce epoch (rotated on issuance)
              </div>
              <div className="font-mono text-xs text-label-secondary">
                {shortHex(event.plan_nonce_epoch_hex)}
              </div>
            </div>
          )}

          {event.error_message && (
            <div className="p-3 bg-red-50 border border-red-200 rounded">
              <div className="text-xs text-red-800 uppercase tracking-wide mb-1">
                Error
              </div>
              <div className="text-sm text-red-900 font-mono break-words">
                {event.error_message}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const ReconcileEvents: React.FC = () => {
  const [siteFilter, setSiteFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['reconcile-events', siteFilter],
    queryFn: () =>
      reconcileApi.events({
        site_id: siteFilter.trim() || undefined,
        limit: 200,
      }),
    refetchInterval: 30_000,
  });

  const filtered = useMemo(() => {
    if (!data?.rows) return [];
    if (statusFilter === 'all') return data.rows;
    return data.rows.filter((r) => r.plan_status === statusFilter);
  }, [data, statusFilter]);

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = { applied: 0, pending: 0, failed: 0, rejected: 0 };
    (data?.rows || []).forEach((r) => {
      counts[r.plan_status] = (counts[r.plan_status] ?? 0) + 1;
    });
    return counts;
  }, [data]);

  return (
    <PageShell
      title="State Reconciliation Events"
      subtitle="Forensic log of appliances that woke in a past state and were reconciled. Used when investigating snapshot reverts, backup restores, or disk-clone deployments. Admin-only."
    >
      <GlassCard>
        <div className="p-4 flex flex-wrap items-end gap-4 border-b border-separator">
          <div className="flex-1 min-w-[240px]">
            <label className="block text-xs text-label-tertiary mb-1">
              Filter by site ID (empty = all)
            </label>
            <input
              type="text"
              value={siteFilter}
              onChange={(e) => setSiteFilter(e.target.value)}
              placeholder="e.g. north-valley-branch-2"
              className="w-full px-3 py-2 bg-fill-quaternary border border-separator rounded text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ios-blue"
            />
          </div>
          <div>
            <label className="block text-xs text-label-tertiary mb-1">Plan status</label>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-3 py-2 bg-fill-quaternary border border-separator rounded text-sm focus:outline-none focus:ring-2 focus:ring-ios-blue"
            >
              <option value="all">All</option>
              <option value="applied">Applied</option>
              <option value="pending">Pending</option>
              <option value="failed">Failed</option>
              <option value="rejected">Rejected</option>
            </select>
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="px-4 py-2 bg-ios-blue text-white rounded text-sm font-medium hover:bg-ios-blue/90 disabled:opacity-50 transition-colors"
          >
            {isFetching ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        {data && (
          <div className="p-4 flex flex-wrap gap-2 text-xs border-b border-separator">
            <span className="px-2 py-1 bg-fill-tertiary rounded">
              Total: {data.count}
            </span>
            <span className="px-2 py-1 bg-green-100 text-green-800 rounded">
              Applied: {statusCounts.applied ?? 0}
            </span>
            <span className="px-2 py-1 bg-amber-100 text-amber-800 rounded">
              Pending: {statusCounts.pending ?? 0}
            </span>
            <span className="px-2 py-1 bg-red-100 text-red-800 rounded">
              Failed: {statusCounts.failed ?? 0}
            </span>
            <span className="px-2 py-1 bg-gray-100 text-gray-700 rounded">
              Rejected: {statusCounts.rejected ?? 0}
            </span>
          </div>
        )}

        {isLoading && (
          <div className="flex items-center justify-center py-12">
            <Spinner />
          </div>
        )}

        {isError && (
          <div className="p-6 text-sm text-health-critical">
            Failed to load reconcile events: {String((error as Error)?.message ?? error)}
          </div>
        )}

        {!isLoading && !isError && filtered.length === 0 && (
          <div className="p-6 text-center text-label-tertiary text-sm">
            {data?.rows.length === 0
              ? 'No state reconciliation events recorded. This is the expected steady state — appliances are not being restored from snapshots.'
              : 'No events match the current filters.'}
          </div>
        )}

        {!isLoading && !isError && filtered.length > 0 && (
          <div>
            {filtered.map((ev) => (
              <EventRow
                key={ev.event_id}
                event={ev}
                expanded={expandedId === ev.event_id}
                onToggle={() =>
                  setExpandedId((prev) => (prev === ev.event_id ? null : ev.event_id))
                }
              />
            ))}
          </div>
        )}
      </GlassCard>
    </PageShell>
  );
};

export default ReconcileEvents;

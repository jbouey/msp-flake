import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Spinner } from '../shared';
import { formatTimeAgo } from '../../constants';

interface ActivityEvent {
  kind: 'admin_action' | 'fleet_order' | 'incident';
  event_id: string;
  at: string | null;
  actor: string | null;
  action: string;
  details?: Record<string, unknown> | null;
}

interface ActivityResponse {
  site_id: string;
  events: ActivityEvent[];
  count: number;
  limit: number;
}

interface SiteActivityTimelineProps {
  siteId: string;
  limit?: number;
}

/**
 * Human-readable action labels. Backend emits MACHINE_CASE action strings;
 * this map keeps display text in one place so it can be audited with the
 * rest of the design system.
 */
const ACTION_LABELS: Record<string, string> = {
  SITE_UPDATED: 'Site updated',
  HEALING_TIER_CHANGED: 'Healing tier changed',
  FLEET_ORDER_CREATED: 'Fleet order queued',
  FLEET_ORDER_DELIVERED: 'Fleet order delivered',
  FLEET_ORDER_COMPLETED: 'Fleet order completed',
  INCIDENT_OPENED: 'Incident opened',
  INCIDENT_RESOLVED: 'Incident resolved',
};

function labelFor(action: string): string {
  return ACTION_LABELS[action] || action.toLowerCase().replace(/_/g, ' ');
}

/**
 * Tiny icon glyph for each event kind — keeps the timeline scannable without
 * spending pixels on real SVGs for every entry.
 */
const KIND_STYLES: Record<ActivityEvent['kind'], { dot: string; label: string }> = {
  admin_action: { dot: 'bg-accent-primary', label: 'Admin' },
  fleet_order: { dot: 'bg-health-warning', label: 'Order' },
  incident: { dot: 'bg-health-critical', label: 'Incident' },
};

/**
 * Render a compact summary of an event's details.
 * - SITE_UPDATED: "tier · small → mid, healing_tier · standard → full_coverage"
 * - HEALING_TIER_CHANGED: "standard → full_coverage"
 * - Fleet order: order_type
 * - Incident: title (truncated)
 */
function summarize(event: ActivityEvent): string | null {
  const d = event.details;
  if (!d || typeof d !== 'object') return null;

  if (event.action === 'SITE_UPDATED') {
    const changes = (d as { changes?: Record<string, { from: unknown; to: unknown }> }).changes;
    if (!changes) return null;
    const parts = Object.entries(changes).map(([field, change]) => {
      const from = change.from === null || change.from === undefined || change.from === '' ? '(empty)' : String(change.from);
      const to = change.to === null || change.to === undefined || change.to === '' ? '(empty)' : String(change.to);
      return `${field} · ${from} → ${to}`;
    });
    return parts.join(', ');
  }

  if (event.action === 'HEALING_TIER_CHANGED') {
    const from = (d as { from?: unknown }).from ?? 'unknown';
    const to = (d as { to?: unknown }).to ?? 'unknown';
    return `${String(from)} → ${String(to)}`;
  }

  if (event.kind === 'fleet_order') {
    const orderType = (d as { order_type?: unknown }).order_type;
    return orderType ? String(orderType) : null;
  }

  if (event.kind === 'incident') {
    const title = (d as { title?: unknown }).title;
    const severity = (d as { severity?: unknown }).severity;
    if (title) return severity ? `${severity}: ${title}` : String(title);
    return severity ? String(severity) : null;
  }

  return null;
}

/**
 * Site activity timeline — a right-rail sidebar widget that surfaces the
 * most recent admin actions, fleet order events and incident transitions
 * for a single site. Fed by the backend `GET /api/sites/{site_id}/activity`
 * endpoint which unions admin_audit_log, fleet_orders and incidents.
 *
 * The component refetches every 2 minutes so operators watching a live
 * remediation see events roll in without manual reload.
 */
export const SiteActivityTimeline: React.FC<SiteActivityTimelineProps> = ({ siteId, limit = 25 }) => {
  const { data, isLoading, error } = useQuery<ActivityResponse>({
    queryKey: ['site-activity', siteId, limit],
    queryFn: async () => {
      const res = await fetch(`/api/sites/${siteId}/activity?limit=${limit}`, {
        credentials: 'same-origin',
      });
      if (!res.ok) {
        throw new Error(`Activity fetch failed: ${res.status}`);
      }
      return res.json();
    },
    refetchInterval: 120_000,
    staleTime: 60_000,
    enabled: !!siteId,
    retry: false,
  });

  return (
    <GlassCard>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Recent Activity</h2>
        {data?.count ? (
          <span className="text-xs text-label-tertiary">{data.count} events</span>
        ) : null}
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-4">
          <Spinner size="sm" />
        </div>
      )}

      {error && !isLoading && (
        <div className="text-xs text-health-critical">
          Failed to load activity
        </div>
      )}

      {!isLoading && !error && data && data.events.length === 0 && (
        <div className="text-xs text-label-tertiary py-2">
          No recorded activity in the last 90 days.
        </div>
      )}

      {!isLoading && data && data.events.length > 0 && (
        <div className="space-y-2.5 max-h-96 overflow-y-auto pr-1">
          {data.events.map((event) => {
            const style = KIND_STYLES[event.kind];
            const summary = summarize(event);
            return (
              <div key={event.event_id} className="flex items-start gap-2.5">
                <span
                  className={`mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0 ${style.dot}`}
                  aria-hidden
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline justify-between gap-2">
                    <p className="text-sm font-medium text-label-primary truncate">
                      {labelFor(event.action)}
                    </p>
                    <span className="text-[11px] text-label-tertiary flex-shrink-0">
                      {formatTimeAgo(event.at)}
                    </span>
                  </div>
                  {summary && (
                    <p className="text-xs text-label-secondary truncate" title={summary}>
                      {summary}
                    </p>
                  )}
                  {event.actor && (
                    <p className="text-[11px] text-label-tertiary">
                      {event.actor}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </GlassCard>
  );
};

export default SiteActivityTimeline;

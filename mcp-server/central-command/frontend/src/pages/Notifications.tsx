import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard, Spinner, Badge } from '../components/shared';
import {
  useNotifications,
  useNotificationSummary,
  useMarkNotificationRead,
  useMarkAllNotificationsRead,
  useDismissNotification,
  useAttentionRequired,
} from '../hooks';
import type { Notification, AttentionItem } from '../types';

type Tab = 'attention' | 'patterns' | 'activity';

function formatRelativeTime(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

// ─── Attention Tab ───────────────────────────────────────────────────────

const typeLabels: Record<string, { label: string; color: string }> = {
  l3_escalation: { label: 'L3 Escalation', color: 'bg-health-critical text-white' },
  repeat_failure: { label: 'Repeat Drift', color: 'bg-health-warning text-white' },
  offline_appliance: { label: 'Offline', color: 'bg-label-tertiary text-white' },
};

const AttentionCard: React.FC<{
  item: AttentionItem;
  onClick: () => void;
}> = ({ item, onClick }) => {
  const meta = typeLabels[item.type] || typeLabels.offline_appliance;
  return (
    <button
      onClick={onClick}
      className="w-full p-4 rounded-ios border border-separator-light bg-white/60 hover:bg-white/80 text-left transition-all"
    >
      <div className="flex items-start gap-3">
        <span className={`flex-shrink-0 px-2 py-0.5 text-[10px] font-bold rounded-full ${meta.color}`}>
          {meta.label}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-label-primary leading-snug">{item.title}</p>
          <p className="text-xs text-label-tertiary mt-1">
            {item.clinic_name && <span className="font-medium text-label-secondary">{item.clinic_name}</span>}
            {item.clinic_name && item.detail && ' — '}
            {item.detail}
          </p>
        </div>
        {item.timestamp && (
          <span className="text-[10px] text-label-tertiary whitespace-nowrap tabular-nums">
            {formatRelativeTime(item.timestamp)}
          </span>
        )}
      </div>
    </button>
  );
};

// ─── Patterns Tab ────────────────────────────────────────────────────────

interface PatternGroup {
  key: string;
  category: string;
  title: string;
  count: number;
  sites: string[];
  latestTime: string;
  severity: string;
}

function groupNotificationsIntoPatterns(notifications: Notification[]): PatternGroup[] {
  const groups = new Map<string, PatternGroup>();

  for (const n of notifications) {
    const key = `${n.category}:${n.severity}`;
    const existing = groups.get(key);
    if (existing) {
      existing.count++;
      if (n.site_id && !existing.sites.includes(n.site_id)) {
        existing.sites.push(n.site_id);
      }
      if (n.created_at > existing.latestTime) {
        existing.latestTime = n.created_at;
      }
    } else {
      groups.set(key, {
        key,
        category: n.category,
        title: n.title,
        count: 1,
        sites: n.site_id ? [n.site_id] : [],
        latestTime: n.created_at,
        severity: n.severity,
      });
    }
  }

  return Array.from(groups.values())
    .sort((a, b) => b.count - a.count);
}

const PatternCard: React.FC<{ pattern: PatternGroup }> = ({ pattern }) => {
  const severityColor =
    pattern.severity === 'critical' ? 'text-health-critical' :
    pattern.severity === 'warning' ? 'text-health-warning' :
    'text-label-secondary';

  return (
    <div className="p-4 rounded-ios border border-separator-light bg-white/60">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-1">
            <Badge variant={
              pattern.severity === 'critical' ? 'error' :
              pattern.severity === 'warning' ? 'warning' : 'default'
            }>
              {pattern.category}
            </Badge>
            <span className={`text-xs font-bold tabular-nums ${severityColor}`}>
              {pattern.count}x
            </span>
          </div>
          <p className="text-sm font-medium text-label-primary">{pattern.title}</p>
          {pattern.sites.length > 0 && (
            <p className="text-xs text-label-tertiary mt-1">
              Across {pattern.sites.length} site{pattern.sites.length !== 1 ? 's' : ''}
              {pattern.sites.length <= 3 && (
                <span className="text-label-secondary"> ({pattern.sites.join(', ')})</span>
              )}
            </p>
          )}
        </div>
        <span className="text-[10px] text-label-tertiary whitespace-nowrap tabular-nums">
          {formatRelativeTime(pattern.latestTime)}
        </span>
      </div>
    </div>
  );
};

// ─── Activity Tab (classic notification view) ────────────────────────────

const NotificationCard: React.FC<{
  notification: Notification;
  onMarkRead: (id: string) => void;
  onDismiss: (id: string) => void;
}> = ({ notification, onMarkRead, onDismiss }) => {
  const severityDot =
    notification.severity === 'critical' ? 'bg-health-critical' :
    notification.severity === 'warning' ? 'bg-health-warning' :
    notification.severity === 'success' ? 'bg-health-healthy' :
    'bg-ios-blue';

  return (
    <div
      className={`p-3.5 rounded-ios border transition-all ${
        notification.is_read
          ? 'bg-white/40 border-separator-light'
          : 'bg-white/70 border-accent-primary/20 shadow-sm'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${severityDot}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <p className={`text-sm leading-snug ${notification.is_read ? 'text-label-secondary' : 'text-label-primary font-medium'}`}>
              {notification.title}
            </p>
            <span className="text-[10px] text-label-tertiary whitespace-nowrap tabular-nums">
              {formatRelativeTime(notification.created_at)}
            </span>
          </div>
          <p className="text-xs text-label-tertiary mt-0.5 line-clamp-2">{notification.message}</p>
          <div className="flex items-center gap-3 mt-2">
            <Badge variant={notification.severity === 'critical' ? 'error' : notification.severity === 'warning' ? 'warning' : 'default'}>
              {notification.category}
            </Badge>
            {notification.site_id && (
              <span className="text-[10px] text-label-tertiary">{notification.site_id}</span>
            )}
            <div className="flex-1" />
            {!notification.is_read && (
              <button onClick={() => onMarkRead(notification.id)} className="text-[10px] text-accent-primary hover:underline">
                Mark read
              </button>
            )}
            <button onClick={() => onDismiss(notification.id)} className="text-[10px] text-label-tertiary hover:text-label-secondary">
              Dismiss
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

// ─── Main Page ───────────────────────────────────────────────────────────

export const Notifications: React.FC = () => {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('attention');

  const { data: notifications = [], isLoading: notifLoading } = useNotifications({});
  const { data: summary } = useNotificationSummary();
  const { data: attention, isLoading: attentionLoading } = useAttentionRequired();
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();
  const dismiss = useDismissNotification();

  const patterns = useMemo(
    () => groupNotificationsIntoPatterns(notifications),
    [notifications]
  );

  const attentionItems = attention?.items ?? [];
  const attentionCount = attention?.count ?? 0;

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: 'attention', label: 'Attention', count: attentionCount },
    { id: 'patterns', label: 'Patterns', count: patterns.length },
    { id: 'activity', label: 'All Activity', count: summary?.total },
  ];

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary">Notifications</h1>
          <p className="text-label-tertiary text-sm mt-0.5">
            {attentionCount > 0
              ? `${attentionCount} item${attentionCount !== 1 ? 's' : ''} need${attentionCount === 1 ? 's' : ''} attention`
              : 'Fleet operating normally'}
          </p>
        </div>
        {summary && summary.unread > 0 && (
          <button
            onClick={() => markAllRead.mutate()}
            className="px-4 py-2 text-sm rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors"
          >
            Mark all read
          </button>
        )}
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 bg-fill-secondary rounded-ios-md p-1 w-fit">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`
              flex items-center gap-1.5 px-3.5 py-1.5 text-sm font-medium rounded-md transition-all
              ${tab === t.id
                ? 'bg-white text-label-primary shadow-sm'
                : 'text-label-tertiary hover:text-label-secondary'
              }
            `}
          >
            {t.label}
            {t.count != null && t.count > 0 && (
              <span className={`
                px-1.5 py-0.5 text-[10px] font-bold rounded-full leading-none
                ${tab === t.id
                  ? t.id === 'attention' && t.count > 0
                    ? 'bg-health-critical text-white'
                    : 'bg-fill-secondary text-label-secondary'
                  : 'bg-separator-light text-label-tertiary'
                }
              `}>
                {t.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'attention' && (
        <GlassCard>
          {attentionLoading ? (
            <div className="py-12 flex justify-center"><Spinner size="lg" /></div>
          ) : attentionItems.length === 0 ? (
            <div className="text-center py-12">
              <div className="w-12 h-12 rounded-full bg-health-healthy/10 flex items-center justify-center mx-auto mb-3">
                <svg className="w-6 h-6 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <h3 className="text-lg font-medium text-label-primary mb-1">All clear</h3>
              <p className="text-sm text-label-tertiary">No items require human attention. The system is handling everything automatically.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {attentionItems.map((item, i) => (
                <AttentionCard
                  key={`${item.type}-${item.site_id}-${i}`}
                  item={item}
                  onClick={() => item.site_id && navigate(`/sites/${item.site_id}`)}
                />
              ))}
            </div>
          )}
        </GlassCard>
      )}

      {tab === 'patterns' && (
        <GlassCard>
          {notifLoading ? (
            <div className="py-12 flex justify-center"><Spinner size="lg" /></div>
          ) : patterns.length === 0 ? (
            <div className="text-center py-12">
              <h3 className="text-lg font-medium text-label-primary mb-1">No patterns</h3>
              <p className="text-sm text-label-tertiary">Notification patterns will appear here as activity accumulates.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {patterns.map((p) => (
                <PatternCard key={p.key} pattern={p} />
              ))}
            </div>
          )}
        </GlassCard>
      )}

      {tab === 'activity' && (
        <GlassCard>
          {notifLoading ? (
            <div className="py-12 flex justify-center"><Spinner size="lg" /></div>
          ) : notifications.length === 0 ? (
            <div className="text-center py-12">
              <svg className="w-12 h-12 mx-auto text-label-tertiary mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
              </svg>
              <h3 className="text-lg font-medium text-label-primary mb-1">All caught up!</h3>
              <p className="text-label-tertiary">No notifications to display.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {notifications.map((notification) => (
                <NotificationCard
                  key={notification.id}
                  notification={notification}
                  onMarkRead={(id) => markRead.mutate(id)}
                  onDismiss={(id) => dismiss.mutate(id)}
                />
              ))}
            </div>
          )}
        </GlassCard>
      )}
    </div>
  );
};

export default Notifications;

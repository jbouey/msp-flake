import React, { useState } from 'react';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { useNotifications, useNotificationSummary, useMarkNotificationRead, useMarkAllNotificationsRead, useDismissNotification, useCreateNotification } from '../hooks';
import type { Notification, NotificationSeverity } from '../types';

/**
 * Format relative time
 */
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

/**
 * Severity icon component
 */
const SeverityIcon: React.FC<{ severity: NotificationSeverity }> = ({ severity }) => {
  const icons = {
    critical: (
      <div className="w-10 h-10 rounded-full bg-red-500/20 flex items-center justify-center">
        <svg className="w-5 h-5 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
      </div>
    ),
    warning: (
      <div className="w-10 h-10 rounded-full bg-yellow-500/20 flex items-center justify-center">
        <svg className="w-5 h-5 text-yellow-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
    ),
    info: (
      <div className="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
        <svg className="w-5 h-5 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
    ),
    success: (
      <div className="w-10 h-10 rounded-full bg-green-500/20 flex items-center justify-center">
        <svg className="w-5 h-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
    ),
  };

  return icons[severity] || icons.info;
};

/**
 * Notification card component
 */
const NotificationCard: React.FC<{
  notification: Notification;
  onMarkRead: (id: string) => void;
  onDismiss: (id: string) => void;
}> = ({ notification, onMarkRead, onDismiss }) => {
  return (
    <div
      className={`p-4 rounded-ios border transition-all ${
        notification.is_read
          ? 'bg-blue-50/30 border-blue-100/50'
          : 'bg-blue-50/60 border-accent-primary/30 shadow-sm'
      }`}
    >
      <div className="flex items-start gap-4">
        <SeverityIcon severity={notification.severity} />

        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div>
              <h3 className={`font-medium ${notification.is_read ? 'text-label-secondary' : 'text-label-primary'}`}>
                {notification.title}
              </h3>
              <p className="text-sm text-label-tertiary mt-0.5">
                {notification.message}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <Badge variant={notification.severity === 'critical' ? 'error' : notification.severity === 'warning' ? 'warning' : 'default'}>
                {notification.category}
              </Badge>
              <span className="text-xs text-label-tertiary whitespace-nowrap">
                {formatRelativeTime(notification.created_at)}
              </span>
            </div>
          </div>

          {notification.site_id && (
            <p className="text-xs text-label-tertiary mt-2">
              Site: {notification.site_id}
            </p>
          )}

          <div className="flex items-center gap-2 mt-3">
            {!notification.is_read && (
              <button
                onClick={() => onMarkRead(notification.id)}
                className="text-xs text-accent-primary hover:text-accent-primary/80"
              >
                Mark as read
              </button>
            )}
            <button
              onClick={() => onDismiss(notification.id)}
              className="text-xs text-label-tertiary hover:text-label-secondary"
            >
              Dismiss
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

/**
 * Notifications page
 */
export const Notifications: React.FC = () => {
  const [filter, setFilter] = useState<string>('all');
  const [showUnreadOnly, setShowUnreadOnly] = useState(false);
  const [showTestModal, setShowTestModal] = useState(false);

  const { data: notifications, isLoading } = useNotifications({
    severity: filter !== 'all' ? filter : undefined,
    unread_only: showUnreadOnly,
  });
  const { data: summary } = useNotificationSummary();
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();
  const dismiss = useDismissNotification();
  const createNotification = useCreateNotification();

  const handleMarkRead = (id: string) => {
    markRead.mutate(id);
  };

  const handleDismiss = (id: string) => {
    dismiss.mutate(id);
  };

  const handleMarkAllRead = () => {
    markAllRead.mutate();
  };

  const handleTestCriticalAlert = () => {
    createNotification.mutate({
      severity: 'critical',
      category: 'security',
      title: 'Test Critical Alert',
      message: 'This is a test critical alert. If SMTP is configured, an email should be sent to administrator@osiriscare.net.',
    });
    setShowTestModal(false);
  };

  const handleTestWarningAlert = () => {
    createNotification.mutate({
      severity: 'warning',
      category: 'compliance',
      title: 'Test Warning Alert',
      message: 'This is a test warning notification to verify the notification system is working.',
    });
    setShowTestModal(false);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary">Notifications</h1>
          <p className="text-label-tertiary text-sm mt-1">
            {summary?.unread || 0} unread notifications
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowTestModal(true)}
            className="px-4 py-2 text-sm rounded-ios bg-blue-50 text-label-primary border border-blue-100 hover:bg-blue-100 transition-colors"
          >
            Test Alert
          </button>
          {summary && summary.unread > 0 && (
            <button
              onClick={handleMarkAllRead}
              className="px-4 py-2 text-sm rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors"
            >
              Mark all as read
            </button>
          )}
        </div>
      </div>

      {/* Test Alert Modal */}
      {showTestModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <GlassCard className="w-full max-w-md p-6">
            <h2 className="text-lg font-semibold text-label-primary mb-4">Test Notification System</h2>
            <p className="text-sm text-label-secondary mb-4">
              Send a test notification to verify the system. Critical alerts will also trigger an email if SMTP is configured.
            </p>
            <div className="space-y-3">
              <button
                onClick={handleTestCriticalAlert}
                disabled={createNotification.isPending}
                className="w-full px-4 py-3 text-sm rounded-ios bg-red-500 text-white hover:bg-red-600 transition-colors flex items-center justify-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                Test Critical Alert (+ Email)
              </button>
              <button
                onClick={handleTestWarningAlert}
                disabled={createNotification.isPending}
                className="w-full px-4 py-3 text-sm rounded-ios bg-yellow-500 text-white hover:bg-yellow-600 transition-colors flex items-center justify-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                Test Warning Alert
              </button>
              <button
                onClick={() => setShowTestModal(false)}
                className="w-full px-4 py-2 text-sm rounded-ios bg-blue-50 text-label-primary border border-blue-100 hover:bg-blue-100 transition-colors"
              >
                Cancel
              </button>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-4 gap-4">
          <GlassCard className="p-4 text-center">
            <div className="text-2xl font-bold text-red-500">{summary.critical}</div>
            <div className="text-xs text-label-tertiary">Critical</div>
          </GlassCard>
          <GlassCard className="p-4 text-center">
            <div className="text-2xl font-bold text-yellow-500">{summary.warning}</div>
            <div className="text-xs text-label-tertiary">Warnings</div>
          </GlassCard>
          <GlassCard className="p-4 text-center">
            <div className="text-2xl font-bold text-blue-500">{summary.info}</div>
            <div className="text-xs text-label-tertiary">Info</div>
          </GlassCard>
          <GlassCard className="p-4 text-center">
            <div className="text-2xl font-bold text-green-500">{summary.success}</div>
            <div className="text-xs text-label-tertiary">Success</div>
          </GlassCard>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-sm text-label-tertiary">Filter:</span>
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="px-3 py-1.5 text-sm rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
          >
            <option value="all">All</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
            <option value="success">Success</option>
          </select>
        </div>
        <label className="flex items-center gap-2 text-sm text-label-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={showUnreadOnly}
            onChange={(e) => setShowUnreadOnly(e.target.checked)}
            className="rounded"
          />
          Unread only
        </label>
      </div>

      {/* Notifications List */}
      <GlassCard>
        {notifications && notifications.length > 0 ? (
          <div className="space-y-3">
            {notifications.map((notification) => (
              <NotificationCard
                key={notification.id}
                notification={notification}
                onMarkRead={handleMarkRead}
                onDismiss={handleDismiss}
              />
            ))}
          </div>
        ) : (
          <div className="text-center py-12">
            <svg className="w-12 h-12 mx-auto text-label-tertiary mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            <h3 className="text-lg font-medium text-label-primary mb-1">All caught up!</h3>
            <p className="text-label-tertiary">No notifications to display.</p>
          </div>
        )}
      </GlassCard>
    </div>
  );
};

export default Notifications;

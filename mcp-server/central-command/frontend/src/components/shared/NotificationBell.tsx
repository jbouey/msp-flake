import React, { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  useNotifications,
  useNotificationSummary,
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
} from '../../hooks';
import type { Notification, NotificationSeverity } from '../../types';

function formatTimeAgo(dateString: string): string {
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

function severityDotColor(severity: NotificationSeverity): string {
  switch (severity) {
    case 'critical':
      return 'bg-health-critical';
    case 'warning':
      return 'bg-health-warning';
    case 'info':
      return 'bg-ios-blue';
    case 'success':
      return 'bg-health-healthy';
    default:
      return 'bg-ios-blue';
  }
}

const NotificationItem: React.FC<{
  notification: Notification;
  onClickItem: (notification: Notification) => void;
}> = ({ notification, onClickItem }) => {
  const siteName =
    (notification.metadata?.site_name as string) ||
    notification.site_id ||
    '';

  return (
    <button
      onClick={() => onClickItem(notification)}
      className="w-full flex items-start gap-3 p-3 hover:bg-fill-secondary rounded-ios-sm cursor-pointer text-left transition-colors"
    >
      <div
        className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${severityDotColor(notification.severity)}`}
      />
      <div className="flex-1 min-w-0">
        <p
          className={`text-sm truncate ${
            notification.is_read
              ? 'text-label-secondary'
              : 'text-label-primary font-medium'
          }`}
        >
          {notification.title}
        </p>
        <p className="text-xs text-label-tertiary mt-0.5 truncate">
          {siteName && <>{siteName} · </>}
          {formatTimeAgo(notification.created_at)}
        </p>
      </div>
    </button>
  );
};

export const NotificationBell: React.FC = () => {
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();

  const { data: summary } = useNotificationSummary();
  const { data: notifications } = useNotifications({ limit: 10 });
  const markAllRead = useMarkAllNotificationsRead();
  const markRead = useMarkNotificationRead();

  const unreadCount = summary?.unread ?? 0;

  // Close on click outside
  useEffect(() => {
    if (!open) return;

    function handleClickOutside(event: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node) &&
        buttonRef.current &&
        !buttonRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;

    function handleEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    }

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [open]);

  function handleClickItem(notification: Notification) {
    if (!notification.is_read) {
      markRead.mutate(notification.id);
    }
    setOpen(false);
    navigate('/notifications');
  }

  function handleMarkAllRead() {
    markAllRead.mutate();
  }

  function handleViewAll() {
    setOpen(false);
    navigate('/notifications');
  }

  return (
    <div className="relative">
      {/* Bell button */}
      <button
        ref={buttonRef}
        onClick={() => setOpen((prev) => !prev)}
        className="relative p-1.5 rounded-ios-sm hover:bg-fill-tertiary transition-colors"
        aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ''}`}
      >
        <svg
          className="w-5 h-5 text-label-secondary hover:text-label-primary transition-colors"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0"
          />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] rounded-full bg-health-critical text-white text-[10px] font-bold flex items-center justify-center px-1">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown panel */}
      {open && (
        <div
          ref={dropdownRef}
          className="absolute right-0 top-full mt-2 w-80 glass-card rounded-ios shadow-lg border border-separator-light z-50 overflow-hidden"
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-separator-light">
            <h3 className="text-sm font-semibold text-label-primary">
              Notifications
            </h3>
            {unreadCount > 0 && (
              <button
                onClick={handleMarkAllRead}
                disabled={markAllRead.isPending}
                className="text-xs text-accent-primary hover:underline disabled:opacity-50"
              >
                Mark all read
              </button>
            )}
          </div>

          {/* Notification list */}
          <div className="max-h-96 overflow-y-auto">
            {!notifications || notifications.length === 0 ? (
              <div className="py-10 text-center">
                <svg
                  className="w-8 h-8 mx-auto text-label-tertiary mb-2"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0"
                  />
                </svg>
                <p className="text-sm text-label-tertiary">
                  No notifications
                </p>
              </div>
            ) : (
              <div className="py-1 px-1">
                {notifications.map((n) => (
                  <NotificationItem
                    key={n.id}
                    notification={n}
                    onClickItem={handleClickItem}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Footer */}
          {notifications && notifications.length > 0 && (
            <div className="border-t border-separator-light px-4 py-2.5">
              <button
                onClick={handleViewAll}
                className="w-full text-center text-xs text-accent-primary hover:underline font-medium"
              >
                View all notifications
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default NotificationBell;

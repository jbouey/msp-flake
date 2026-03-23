import React, { memo } from 'react';
import { LevelBadge } from '../shared';
import { CHECK_TYPE_LABELS } from '../../types';
import type { Incident } from '../../types';

interface IncidentRowProps {
  incident: Incident;
  onClick?: () => void;
  compact?: boolean;
  onResolve?: (id: string) => void;
  onEscalate?: (id: string) => void;
  onSuppress?: (id: string) => void;
  actionLoading?: string | null;
}

const formatTime = (dateStr: string): string => {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;

  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
};

const formatTimeShort = (dateStr: string): string => {
  const date = new Date(dateStr);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

/** Inline SVG icons for action buttons */
const CheckIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
  </svg>
);

const ArrowUpIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M5 10l7-7m0 0l7 7m-7-7v18" />
  </svg>
);

const BellSlashIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6 6 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
    <line x1="3" y1="3" x2="21" y2="21" strokeLinecap="round" />
  </svg>
);

export const IncidentRow: React.FC<IncidentRowProps> = memo(({
  incident,
  onClick,
  compact = false,
  onResolve,
  onEscalate,
  onSuppress,
  actionLoading,
}) => {
  const checkLabel = CHECK_TYPE_LABELS[incident.check_type] || incident.check_type;
  const isLoading = actionLoading === String(incident.id);
  const showActions = !compact && !incident.resolved && (onResolve || onEscalate || onSuppress);

  /** Stop event propagation so row click (expand) does not fire */
  const handleAction = (e: React.MouseEvent, action: ((id: string) => void) | undefined) => {
    e.stopPropagation();
    if (action && !isLoading) {
      action(String(incident.id));
    }
  };

  if (compact) {
    return (
      <button
        onClick={onClick}
        className="w-full flex items-center gap-3 py-2 px-3 hover:bg-separator-light rounded-ios-sm transition-colors text-left"
      >
        <span className="text-xs text-label-tertiary w-12">
          {formatTimeShort(incident.created_at)}
        </span>
        <span className="text-sm text-label-primary truncate flex-1">
          {incident.hostname}
        </span>
        <span className="text-sm text-label-secondary truncate max-w-[100px]">
          {checkLabel}
        </span>
        {incident.resolution_level && (
          <LevelBadge level={incident.resolution_level} />
        )}
        {incident.resolved ? (
          <svg className="w-4 h-4 text-health-healthy flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        ) : (
          <svg className="w-4 h-4 text-health-warning flex-shrink-0 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        )}
      </button>
    );
  }

  return (
    <button
      onClick={onClick}
      className="group w-full flex items-center gap-4 p-4 bg-fill-secondary hover:bg-fill-primary rounded-ios-md transition-colors text-left border border-separator-light"
    >
      {/* Status indicator */}
      <span
        className={`status-dot ${
          incident.resolved ? 'status-dot-healthy' : 'status-dot-warning animate-pulse-soft'
        }`}
      />

      {/* Time */}
      <div className="w-16 flex-shrink-0">
        <p className="text-sm font-medium text-label-primary">
          {formatTimeShort(incident.created_at)}
        </p>
        <p className="text-xs text-label-tertiary">
          {formatTime(incident.created_at)}
        </p>
      </div>

      {/* Client & Host */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-label-primary truncate">
          {incident.site_id.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
        </p>
        <p className="text-xs text-label-tertiary truncate">
          {incident.hostname}
        </p>
      </div>

      {/* Check type */}
      <div className="w-24 flex-shrink-0">
        <p className="text-sm text-label-secondary">{checkLabel}</p>
        {incident.hipaa_controls.length > 0 && (
          <p className="text-xs text-label-tertiary truncate">
            {incident.hipaa_controls[0]}
          </p>
        )}
      </div>

      {/* Resolution level */}
      <div className="w-20 flex-shrink-0">
        {incident.resolution_level ? (
          <LevelBadge level={incident.resolution_level} showLabel />
        ) : (
          <span className="text-xs text-label-tertiary">Pending</span>
        )}
      </div>

      {/* Inline action buttons */}
      {showActions ? (
        <div className="flex items-center gap-1 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity flex-shrink-0">
          {isLoading ? (
            <span className="w-20 flex justify-center">
              <svg className="w-4 h-4 animate-spin text-label-tertiary" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="31.4 31.4" strokeLinecap="round" />
              </svg>
            </span>
          ) : (
            <>
              {onResolve && (
                <button
                  onClick={(e) => handleAction(e, onResolve)}
                  className="p-1.5 rounded-ios-sm hover:bg-health-healthy/10 text-health-healthy transition-colors"
                  title="Resolve"
                >
                  <CheckIcon className="w-4 h-4" />
                </button>
              )}
              {onEscalate && (
                <button
                  onClick={(e) => handleAction(e, onEscalate)}
                  className="p-1.5 rounded-ios-sm hover:bg-ios-orange/10 text-ios-orange transition-colors"
                  title="Escalate to L3"
                >
                  <ArrowUpIcon className="w-4 h-4" />
                </button>
              )}
              {onSuppress && (
                <button
                  onClick={(e) => handleAction(e, onSuppress)}
                  className="p-1.5 rounded-ios-sm hover:bg-label-tertiary/10 text-label-tertiary transition-colors"
                  title="Suppress 24h"
                >
                  <BellSlashIcon className="w-4 h-4" />
                </button>
              )}
            </>
          )}
        </div>
      ) : (
        /* Status (shown when resolved or no action handlers) */
        <div className="w-16 flex-shrink-0 text-right">
          {incident.resolved ? (
            <span className="text-sm text-health-healthy font-medium">Resolved</span>
          ) : (
            <span className="text-sm text-health-warning font-medium">Active</span>
          )}
        </div>
      )}
    </button>
  );
});

export default IncidentRow;

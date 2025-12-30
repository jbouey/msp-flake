import React from 'react';
import { LevelBadge } from '../shared';
import type { Incident } from '../../types';

interface IncidentRowProps {
  incident: Incident;
  onClick?: () => void;
  compact?: boolean;
}

const checkTypeLabels: Record<string, string> = {
  patching: 'Patch',
  antivirus: 'AV',
  backup: 'Backup',
  logging: 'Logging',
  firewall: 'Firewall',
  encryption: 'Encryption',
};

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

export const IncidentRow: React.FC<IncidentRowProps> = ({
  incident,
  onClick,
  compact = false,
}) => {
  const checkLabel = checkTypeLabels[incident.check_type] || incident.check_type;

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
      className="w-full flex items-center gap-4 p-4 bg-white/50 hover:bg-white/80 rounded-ios-md transition-colors text-left border border-separator-light"
    >
      {/* Status indicator */}
      <div
        className={`w-2 h-2 rounded-full flex-shrink-0 ${
          incident.resolved ? 'bg-health-healthy' : 'bg-health-warning animate-pulse'
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
        <p className="text-sm text-label-secondary">{checkLabel} drift</p>
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

      {/* Status */}
      <div className="w-16 flex-shrink-0 text-right">
        {incident.resolved ? (
          <span className="text-sm text-health-healthy font-medium">Resolved</span>
        ) : (
          <span className="text-sm text-health-warning font-medium">Active</span>
        )}
      </div>
    </button>
  );
};

export default IncidentRow;

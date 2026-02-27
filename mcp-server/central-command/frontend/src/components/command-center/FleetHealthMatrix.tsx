import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard, Spinner } from '../shared';
import { useFleetPosture } from '../../hooks';
import { getHealthColor, getHealthStatus } from '../../tokens/style-tokens';
import type { FleetPostureSite } from '../../types';

function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return 'Never';
  const now = Date.now();
  const then = new Date(dateString).getTime();
  const diffMin = Math.floor((now - then) / 60000);
  if (diffMin < 1) return 'Now';
  if (diffMin < 60) return `${diffMin}m`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h`;
  return `${Math.floor(diffHrs / 24)}d`;
}

const TrendIndicator: React.FC<{ trend: string }> = ({ trend }) => {
  if (trend === 'improving') {
    return (
      <svg className="w-3.5 h-3.5 text-health-healthy" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M7 11V3M3.5 6.5L7 3l3.5 3.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (trend === 'declining') {
    return (
      <svg className="w-3.5 h-3.5 text-health-critical" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M7 3v8M3.5 7.5L7 11l3.5-3.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg className="w-3.5 h-3.5 text-label-tertiary" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 7h10" strokeLinecap="round" />
    </svg>
  );
};

const ComplianceBar: React.FC<{ score: number }> = ({ score }) => {
  const status = getHealthStatus(score);
  const color = getHealthColor(status);
  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="flex-1 h-1.5 bg-separator-light rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${Math.min(score, 100)}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs font-semibold tabular-nums w-8 text-right" style={{ color }}>
        {Math.round(score)}%
      </span>
    </div>
  );
};

const SiteRow: React.FC<{
  site: FleetPostureSite;
  onClick: () => void;
}> = ({ site, onClick }) => {
  const hasAttention = site.l3_unresolved > 0 || site.unresolved > 0;
  const isOffline = site.online_count === 0 && site.appliance_count > 0;

  return (
    <button
      onClick={onClick}
      className={`
        w-full grid grid-cols-[1fr_80px_100px_60px_60px_130px_32px] items-center gap-2 px-3 py-2.5
        text-left transition-all duration-150 rounded-ios-md
        ${hasAttention
          ? 'bg-red-50/50 hover:bg-red-50/80 border border-red-100/50'
          : 'hover:bg-fill-secondary border border-transparent'
        }
      `}
    >
      {/* Site name + status */}
      <div className="flex items-center gap-2 min-w-0">
        <div
          className={`w-2 h-2 rounded-full flex-shrink-0 ${isOffline ? 'bg-health-critical' : 'bg-health-healthy'}`}
          title={isOffline ? 'Offline' : 'Online'}
        />
        <span className="text-sm font-medium text-label-primary truncate">
          {site.clinic_name}
        </span>
        {site.l3_unresolved > 0 && (
          <span className="flex-shrink-0 px-1.5 py-0.5 text-[10px] font-bold bg-health-critical text-white rounded-full leading-none">
            L3
          </span>
        )}
      </div>

      {/* Appliances */}
      <span className="text-xs text-label-secondary tabular-nums text-center">
        <span className={site.online_count < site.appliance_count ? 'text-health-warning' : 'text-health-healthy'}>
          {site.online_count}
        </span>
        <span className="text-label-tertiary">/{site.appliance_count}</span>
      </span>

      {/* Last checkin */}
      <span className="text-xs text-label-tertiary tabular-nums text-center">
        {formatRelativeTime(site.last_checkin)}
      </span>

      {/* Incidents 24h */}
      <span className={`text-xs font-semibold tabular-nums text-center ${
        site.incidents_24h > 10 ? 'text-health-warning' :
        site.incidents_24h > 0 ? 'text-label-secondary' : 'text-label-tertiary'
      }`}>
        {site.incidents_24h || '-'}
      </span>

      {/* Unresolved */}
      <span className={`text-xs font-semibold tabular-nums text-center ${
        site.unresolved > 0 ? 'text-health-critical' : 'text-label-tertiary'
      }`}>
        {site.unresolved || '-'}
      </span>

      {/* Compliance bar */}
      <ComplianceBar score={site.compliance_score} />

      {/* Trend */}
      <div className="flex justify-center">
        <TrendIndicator trend={site.trend} />
      </div>
    </button>
  );
};

export const FleetHealthMatrix: React.FC<{ className?: string }> = ({ className = '' }) => {
  const navigate = useNavigate();
  const { data: sites, isLoading } = useFleetPosture();

  const totalSites = sites?.length ?? 0;
  const needsAttention = sites?.filter(s => s.l3_unresolved > 0 || s.unresolved > 0).length ?? 0;

  return (
    <GlassCard className={className}>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-base font-semibold text-label-primary">Fleet Posture</h3>
          <p className="text-xs text-label-tertiary mt-0.5">
            {totalSites} site{totalSites !== 1 ? 's' : ''}
            {needsAttention > 0 && (
              <span className="text-health-critical font-medium"> — {needsAttention} need{needsAttention !== 1 ? '' : 's'} attention</span>
            )}
          </p>
        </div>
      </div>

      {/* Column headers */}
      <div className="grid grid-cols-[1fr_80px_100px_60px_60px_130px_32px] gap-2 px-3 pb-2 border-b border-separator-light">
        {['Site', 'Appliances', 'Last Seen', '24h', 'Open', 'Compliance', ''].map((h) => (
          <span key={h} className="text-[10px] font-semibold uppercase tracking-wider text-label-tertiary text-center first:text-left">
            {h}
          </span>
        ))}
      </div>

      {/* Rows */}
      <div className="divide-y divide-separator-light/50 max-h-[340px] overflow-y-auto">
        {isLoading ? (
          <div className="py-8 flex justify-center"><Spinner size="sm" /></div>
        ) : !sites?.length ? (
          <div className="py-8 text-center text-sm text-label-tertiary">
            No sites configured
          </div>
        ) : (
          sites.map((site) => (
            <SiteRow
              key={site.site_id}
              site={site}
              onClick={() => navigate(`/sites/${site.site_id}`)}
            />
          ))
        )}
      </div>
    </GlassCard>
  );
};

export default FleetHealthMatrix;

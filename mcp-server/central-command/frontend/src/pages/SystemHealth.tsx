import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, StatCard, Spinner } from '../components/shared';

// -- Types --

interface TableInfo {
  table_name: string;
  row_count: number;
  total_size: string;
}

interface FleetEntry {
  site_id: string;
  clinic_name: string | null;
  agent_version: string | null;
  status: string;
  last_checkin: string | null;
}

interface SystemHealthData {
  status: string;
  checked_at: string;
  database: {
    size: string;
    connections: Record<string, number>;
    top_tables: TableInfo[];
  };
  l2_api: { calls_24h: number };
  errors: { critical_1h: number };
  fleet: FleetEntry[];
  background_tasks: Record<string, { status: string }>;
}

// -- Icons --

const ServerIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 14.25h13.5m-13.5 0a3 3 0 01-3-3m3 3a3 3 0 100 6h13.5a3 3 0 100-6m-16.5-3a3 3 0 013-3h13.5a3 3 0 013 3m-19.5 0a4.5 4.5 0 01.9-2.7L5.737 5.1a3.375 3.375 0 012.7-1.35h7.126c1.062 0 2.062.5 2.7 1.35l2.587 3.45a4.5 4.5 0 01.9 2.7m0 0a3 3 0 01-3 3m0 3h.008v.008h-.008v-.008zm0-6h.008v.008h-.008v-.008zm-3 6h.008v.008h-.008v-.008zm0-6h.008v.008h-.008v-.008z" />
  </svg>
);

const DatabaseIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125" />
  </svg>
);

// -- Badges --

const StatusBanner: React.FC<{ status: string; checkedAt: string }> = ({ status, checkedAt }) => {
  const config: Record<string, { bg: string; text: string; dot: string; label: string }> = {
    healthy: { bg: 'bg-health-healthy/10', text: 'text-health-healthy', dot: 'bg-health-healthy', label: 'All Systems Healthy' },
    degraded: { bg: 'bg-health-warning/10', text: 'text-health-warning', dot: 'bg-health-warning', label: 'Degraded Performance' },
    critical: { bg: 'bg-health-critical/10', text: 'text-health-critical', dot: 'bg-health-critical', label: 'Critical Issues' },
  };
  const c = config[status] || config.degraded;

  return (
    <GlassCard>
      <div className={`p-5 ${c.bg} rounded-ios-lg`}>
        <div className="flex items-center gap-3">
          <span className={`w-3 h-3 rounded-full ${c.dot} animate-pulse`} />
          <span className={`text-xl font-bold font-display ${c.text}`}>{c.label}</span>
        </div>
        <p className="text-xs text-label-tertiary mt-2">
          Last checked: {new Date(checkedAt).toLocaleString()}
        </p>
      </div>
    </GlassCard>
  );
};

const TaskBadge: React.FC<{ status: string }> = ({ status }) => {
  const isCrashed = status.startsWith('crashed');
  const isRunning = status === 'running';
  const cls = isCrashed
    ? 'bg-health-critical/10 text-health-critical'
    : isRunning
    ? 'bg-health-healthy/10 text-health-healthy'
    : 'bg-fill-secondary text-label-tertiary';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {isRunning && <span className="w-1.5 h-1.5 rounded-full bg-health-healthy mr-1.5 animate-pulse" />}
      {status}
    </span>
  );
};

const FleetBadge: React.FC<{ status: string }> = ({ status }) => {
  const cls: Record<string, string> = {
    online: 'bg-health-healthy/10 text-health-healthy',
    stale: 'bg-health-warning/10 text-health-warning',
    offline: 'bg-health-critical/10 text-health-critical',
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${cls[status] || cls.offline}`}>
      {status === 'online' && <span className="w-1.5 h-1.5 rounded-full bg-health-healthy mr-1.5 animate-pulse" />}
      {status}
    </span>
  );
};

function formatTimeAgo(dateStr: string | null): string {
  if (!dateStr) return '--';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// -- Main --

export const SystemHealth: React.FC = () => {
  const {
    data,
    isLoading,
    error,
  } = useQuery<SystemHealthData>({
    queryKey: ['system-health'],
    queryFn: async () => {
      const res = await fetch('/api/dashboard/admin/system-health', {
        credentials: 'same-origin',
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: 'Request failed' }));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      return res.json();
    },
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3">
        <Spinner size="lg" />
        <p className="text-sm text-label-tertiary">Loading system health...</p>
      </div>
    );
  }

  if (error) {
    return (
      <GlassCard>
        <div className="p-5 text-center">
          <p className="text-health-critical font-medium">Failed to load system health</p>
          <p className="text-sm text-label-tertiary mt-1">{(error as Error).message}</p>
        </div>
      </GlassCard>
    );
  }

  if (!data) return null;

  const totalConns = Object.values(data.database.connections).reduce((a, b) => a + b, 0);
  const activeConns = data.database.connections['active'] || 0;

  return (
    <div className="space-y-6">
      {/* Status Banner */}
      <StatusBanner status={data.status} checkedAt={data.checked_at} />

      {/* Quick Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Critical Errors (1h)"
          value={data.errors.critical_1h}
          color={data.errors.critical_1h > 0 ? '#EF4444' : '#14A89E'}
          icon={
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke={data.errors.critical_1h > 0 ? '#EF4444' : '#14A89E'} strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
            </svg>
          }
        />
        <StatCard
          label="L2 API Calls (24h)"
          value={data.l2_api.calls_24h}
          color="#3B82F6"
          icon={
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="#3B82F6" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
            </svg>
          }
        />
        <StatCard
          label="Database Size"
          value={data.database.size}
          color="#8B5CF6"
          icon={<DatabaseIcon className="w-4 h-4 text-[#8B5CF6]" />}
        />
        <StatCard
          label="DB Connections"
          value={`${activeConns} / ${totalConns}`}
          color="#F59E0B"
          icon={<ServerIcon className="w-4 h-4 text-[#F59E0B]" />}
        />
      </div>

      {/* Background Tasks + DB Connections side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Background Tasks */}
        <GlassCard>
          <div className="p-5">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-label-secondary mb-4">
              Background Tasks
            </h2>
            {Object.keys(data.background_tasks).length === 0 ? (
              <p className="text-sm text-label-tertiary">No background tasks running</p>
            ) : (
              <div className="space-y-2">
                {Object.entries(data.background_tasks).map(([name, info]) => (
                  <div key={name} className="flex items-center justify-between py-2 border-b border-border-primary/50 last:border-0">
                    <span className="text-sm font-medium text-label-primary">{name}</span>
                    <TaskBadge status={info.status} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </GlassCard>

        {/* DB Connections */}
        <GlassCard>
          <div className="p-5">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-label-secondary mb-4">
              Connection States
            </h2>
            <div className="space-y-2">
              {Object.entries(data.database.connections).map(([state, count]) => (
                <div key={state} className="flex items-center justify-between py-2 border-b border-border-primary/50 last:border-0">
                  <span className="text-sm text-label-primary capitalize">{state || 'unknown'}</span>
                  <span className="text-sm font-medium text-label-secondary tabular-nums">{count}</span>
                </div>
              ))}
              <div className="flex items-center justify-between pt-2">
                <span className="text-sm font-medium text-label-primary">Total</span>
                <span className="text-sm font-bold text-label-primary tabular-nums">{totalConns}</span>
              </div>
            </div>
          </div>
        </GlassCard>
      </div>

      {/* Top Tables */}
      <GlassCard>
        <div className="p-5">
          <div className="flex items-center gap-2 mb-4">
            <DatabaseIcon className="w-4 h-4 text-label-secondary" />
            <h2 className="text-sm font-semibold uppercase tracking-wider text-label-secondary">
              Top 10 Tables by Size
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-primary">
                  <th className="text-left py-2 pr-4 font-medium text-label-secondary">Table</th>
                  <th className="text-right py-2 px-4 font-medium text-label-secondary">Rows</th>
                  <th className="text-right py-2 pl-4 font-medium text-label-secondary">Size</th>
                </tr>
              </thead>
              <tbody>
                {data.database.top_tables.map((t) => (
                  <tr key={t.table_name} className="border-b border-border-primary/50">
                    <td className="py-2 pr-4 text-label-primary font-mono text-xs">{t.table_name}</td>
                    <td className="py-2 px-4 text-right text-label-secondary tabular-nums">
                      {Number(t.row_count).toLocaleString()}
                    </td>
                    <td className="py-2 pl-4 text-right text-label-secondary">{t.total_size}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </GlassCard>

      {/* Fleet Status */}
      <GlassCard>
        <div className="p-5">
          <div className="flex items-center gap-2 mb-4">
            <ServerIcon className="w-4 h-4 text-label-secondary" />
            <h2 className="text-sm font-semibold uppercase tracking-wider text-label-secondary">
              Fleet Status
            </h2>
          </div>
          {data.fleet.length === 0 ? (
            <p className="text-sm text-label-tertiary">No appliances registered</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border-primary">
                    <th className="text-left py-2 pr-4 font-medium text-label-secondary">Site</th>
                    <th className="text-left py-2 px-4 font-medium text-label-secondary">Version</th>
                    <th className="text-left py-2 px-4 font-medium text-label-secondary">Status</th>
                    <th className="text-right py-2 pl-4 font-medium text-label-secondary">Last Checkin</th>
                  </tr>
                </thead>
                <tbody>
                  {data.fleet.map((f) => (
                    <tr key={f.site_id} className="border-b border-border-primary/50">
                      <td className="py-2 pr-4 text-label-primary font-medium">
                        {f.clinic_name || f.site_id}
                      </td>
                      <td className="py-2 px-4 text-label-secondary font-mono text-xs">
                        {f.agent_version || '--'}
                      </td>
                      <td className="py-2 px-4">
                        <FleetBadge status={f.status} />
                      </td>
                      <td className="py-2 pl-4 text-right text-label-tertiary text-xs">
                        {formatTimeAgo(f.last_checkin)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </GlassCard>

      {/* Auto-refresh indicator */}
      <div className="text-center">
        <p className="text-xs text-label-tertiary">Auto-refreshes every 30 seconds</p>
      </div>
    </div>
  );
};

export default SystemHealth;

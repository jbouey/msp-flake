import React, { useState, useCallback, useRef } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { GlassCard } from '../components/shared';
import { logsApi } from '../utils/api';
import type { LogEntry, LogSearchResponse } from '../utils/api';
import { useSites } from '../hooks/useFleet';

const PRIORITY_COLORS: Record<number, string> = {
  0: 'text-red-400 bg-red-900/30',    // emerg
  1: 'text-red-400 bg-red-900/30',    // alert
  2: 'text-red-300 bg-red-900/20',    // crit
  3: 'text-orange-400 bg-orange-900/20', // err
  4: 'text-yellow-400 bg-yellow-900/20', // warning
  5: 'text-blue-300 bg-blue-900/20',  // notice
  6: 'text-label-secondary bg-fill-secondary', // info
  7: 'text-label-tertiary bg-fill-tertiary',   // debug
};

const PRIORITY_LABELS: Record<number, string> = {
  0: 'EMERG', 1: 'ALERT', 2: 'CRIT', 3: 'ERR',
  4: 'WARN', 5: 'NOTICE', 6: 'INFO', 7: 'DEBUG',
};

const formatTimestamp = (ts: string): string => {
  const d = new Date(ts);
  return d.toLocaleString([], {
    month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  });
};

const TIME_RANGES = [
  { label: 'Last 15m', value: 15 },
  { label: 'Last 1h', value: 60 },
  { label: 'Last 6h', value: 360 },
  { label: 'Last 24h', value: 1440 },
  { label: 'Last 7d', value: 10080 },
  { label: 'Last 30d', value: 43200 },
];

export const LogExplorer: React.FC = () => {
  const [siteId, setSiteId] = useState('');
  const [timeRange, setTimeRange] = useState(1440); // 24h default
  const [unit, setUnit] = useState('');
  const [priority, setPriority] = useState<number | ''>('');
  const [searchText, setSearchText] = useState('');
  const [pendingSearch, setPendingSearch] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize] = useState(100);
  const searchTimer = useRef<ReturnType<typeof setTimeout>>();

  // Fetch sites for dropdown
  const { data: sitesData } = useSites();
  const sites = sitesData?.sites || [];

  // Auto-select first site
  React.useEffect(() => {
    if (sites.length > 0 && !siteId) {
      setSiteId(sites[0].site_id);
    }
  }, [sites, siteId]);

  // Build time params
  const now = new Date();
  const start = new Date(now.getTime() - timeRange * 60000).toISOString();

  // Fetch units for filter
  const { data: units } = useQuery<string[]>({
    queryKey: ['log-units', siteId],
    queryFn: () => logsApi.getUnits(siteId),
    enabled: !!siteId,
    staleTime: 60000,
  });

  // Fetch logs
  const { data, isLoading, isFetching } = useQuery<LogSearchResponse>({
    queryKey: ['logs', siteId, start, unit, priority, searchText, page, pageSize],
    queryFn: () => logsApi.search({
      site_id: siteId,
      start,
      unit: unit || undefined,
      priority: priority === '' ? undefined : priority,
      q: searchText || undefined,
      limit: pageSize,
      offset: page * pageSize,
    }),
    enabled: !!siteId,
    refetchInterval: 30000, // Auto-refresh every 30s
    placeholderData: keepPreviousData,
  });

  const logs = data?.logs || [];
  const total = data?.total || 0;
  const totalPages = Math.ceil(total / pageSize);

  const handleSearchInput = useCallback((value: string) => {
    setPendingSearch(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setSearchText(value);
      setPage(0);
    }, 400);
  }, []);

  const handleExport = (format: 'csv' | 'json') => {
    const url = logsApi.exportUrl({
      site_id: siteId,
      start,
      unit: unit || undefined,
      priority: priority === '' ? undefined : priority,
      q: searchText || undefined,
      format,
    });
    window.open(url, '_blank');
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Log Explorer</h1>
          <p className="text-label-secondary text-sm mt-1">
            Centralized log aggregation across all appliances
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => handleExport('csv')}
            disabled={!siteId || total === 0}
            className="btn-secondary text-sm px-3 py-1.5 disabled:opacity-40"
          >
            Export CSV
          </button>
          <button
            onClick={() => handleExport('json')}
            disabled={!siteId || total === 0}
            className="btn-secondary text-sm px-3 py-1.5 disabled:opacity-40"
          >
            Export JSON
          </button>
        </div>
      </div>

      {/* Filters */}
      <GlassCard padding="md">
        <div className="flex flex-wrap items-center gap-3">
          {/* Site selector */}
          <select
            value={siteId}
            onChange={(e) => { setSiteId(e.target.value); setUnit(''); setPage(0); }}
            className="px-3 py-2 bg-fill-secondary border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary text-sm w-48"
          >
            <option value="">Select site...</option>
            {sites.map((s) => (
              <option key={s.site_id} value={s.site_id}>{s.clinic_name || s.site_id}</option>
            ))}
          </select>

          {/* Time range */}
          <select
            value={timeRange}
            onChange={(e) => { setTimeRange(Number(e.target.value)); setPage(0); }}
            className="px-3 py-2 bg-fill-secondary border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary text-sm w-36"
          >
            {TIME_RANGES.map((r) => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>

          {/* Unit filter */}
          <select
            value={unit}
            onChange={(e) => { setUnit(e.target.value); setPage(0); }}
            className="px-3 py-2 bg-fill-secondary border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary text-sm w-44"
          >
            <option value="">All units</option>
            {(units || []).map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>

          {/* Priority filter */}
          <select
            value={priority}
            onChange={(e) => { setPriority(e.target.value === '' ? '' : Number(e.target.value)); setPage(0); }}
            className="px-3 py-2 bg-fill-secondary border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary text-sm w-36"
          >
            <option value="">All priorities</option>
            {[0,1,2,3,4,5,6,7].map((p) => (
              <option key={p} value={p}>{PRIORITY_LABELS[p]} ({p})</option>
            ))}
          </select>

          {/* Search */}
          <div className="flex-1 min-w-[200px]">
            <input
              type="text"
              value={pendingSearch}
              onChange={(e) => handleSearchInput(e.target.value)}
              placeholder="Search log messages..."
              className="px-3 py-2 bg-fill-secondary border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary text-sm w-full"
            />
          </div>

          {/* Result count */}
          <span className="text-label-tertiary text-xs whitespace-nowrap">
            {isFetching ? 'Loading...' : `${total.toLocaleString()} entries`}
          </span>
        </div>
      </GlassCard>

      {/* Log table */}
      <GlassCard padding="none">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-separator-primary text-label-tertiary text-xs uppercase tracking-wider">
                <th className="px-3 py-2 text-left w-40">Timestamp</th>
                <th className="px-3 py-2 text-left w-16">Level</th>
                <th className="px-3 py-2 text-left w-32">Hostname</th>
                <th className="px-3 py-2 text-left w-40">Unit</th>
                <th className="px-3 py-2 text-left">Message</th>
              </tr>
            </thead>
            <tbody className="font-mono text-xs">
              {isLoading && !logs.length ? (
                <tr>
                  <td colSpan={5} className="px-3 py-12 text-center text-label-tertiary">
                    Loading logs...
                  </td>
                </tr>
              ) : logs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-12 text-center text-label-tertiary">
                    {siteId ? 'No log entries found for the selected filters.' : 'Select a site to view logs.'}
                  </td>
                </tr>
              ) : (
                logs.map((log) => (
                  <LogRow key={log.id} log={log} />
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-3 py-2 border-t border-separator-primary">
            <span className="text-label-tertiary text-xs">
              Page {page + 1} of {totalPages}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => setPage(0)}
                disabled={page === 0}
                className="btn-secondary text-xs px-2 py-1 disabled:opacity-40"
              >
                First
              </button>
              <button
                onClick={() => setPage(page - 1)}
                disabled={page === 0}
                className="btn-secondary text-xs px-2 py-1 disabled:opacity-40"
              >
                Prev
              </button>
              <button
                onClick={() => setPage(page + 1)}
                disabled={page >= totalPages - 1}
                className="btn-secondary text-xs px-2 py-1 disabled:opacity-40"
              >
                Next
              </button>
              <button
                onClick={() => setPage(totalPages - 1)}
                disabled={page >= totalPages - 1}
                className="btn-secondary text-xs px-2 py-1 disabled:opacity-40"
              >
                Last
              </button>
            </div>
          </div>
        )}
      </GlassCard>
    </div>
  );
};

const LogRow: React.FC<{ log: LogEntry }> = React.memo(({ log }) => {
  const [expanded, setExpanded] = useState(false);
  const isLong = log.message.length > 200;

  return (
    <tr
      className="border-b border-separator-primary/50 hover:bg-fill-secondary/30 cursor-pointer"
      onClick={() => isLong && setExpanded(!expanded)}
    >
      <td className="px-3 py-1.5 text-label-secondary whitespace-nowrap">
        {formatTimestamp(log.timestamp)}
      </td>
      <td className="px-3 py-1.5">
        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${PRIORITY_COLORS[log.priority] || ''}`}>
          {PRIORITY_LABELS[log.priority] || log.priority}
        </span>
      </td>
      <td className="px-3 py-1.5 text-label-secondary truncate max-w-[8rem]" title={log.hostname}>
        {log.hostname}
      </td>
      <td className="px-3 py-1.5 text-ios-blue truncate max-w-[10rem]" title={log.unit}>
        {log.unit}
      </td>
      <td className="px-3 py-1.5 text-label-primary">
        {expanded ? (
          <pre className="whitespace-pre-wrap break-all">{log.message}</pre>
        ) : (
          <span className="truncate block max-w-[600px]" title={isLong ? 'Click to expand' : undefined}>
            {log.message.length > 200 ? log.message.slice(0, 200) + '...' : log.message}
          </span>
        )}
      </td>
    </tr>
  );
});

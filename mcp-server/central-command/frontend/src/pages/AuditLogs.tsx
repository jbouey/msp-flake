import React, { useState } from 'react';
import { GlassCard } from '../components/shared';
import { useAuth } from '../contexts/AuthContext';

const formatDateTime = (dateStr: string): string => {
  const date = new Date(dateStr);
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
};

const getActionColor = (action: string): string => {
  switch (action) {
    case 'LOGIN':
      return 'text-health-healthy bg-green-50';
    case 'LOGOUT':
      return 'text-label-secondary bg-gray-100';
    case 'CREATE':
    case 'UPDATE':
      return 'text-ios-blue bg-blue-50';
    case 'DELETE':
      return 'text-health-critical bg-red-50';
    case 'EXECUTE':
      return 'text-ios-purple bg-purple-50';
    case 'VIEW':
      return 'text-label-tertiary bg-gray-50';
    default:
      return 'text-label-secondary bg-gray-100';
  }
};

export const AuditLogs: React.FC = () => {
  const { auditLogs, user } = useAuth();
  const [filterAction, setFilterAction] = useState<string>('all');
  const [filterUser, setFilterUser] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');

  // Get unique actions and users for filters
  const actions = Array.from(new Set(auditLogs.map((log) => log.action)));
  const users = Array.from(new Set(auditLogs.map((log) => log.user)));

  // Filter logs
  const filteredLogs = auditLogs.filter((log) => {
    const matchesAction = filterAction === 'all' || log.action === filterAction;
    const matchesUser = filterUser === 'all' || log.user === filterUser;
    const matchesSearch =
      searchQuery === '' ||
      log.target.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (log.details?.toLowerCase().includes(searchQuery.toLowerCase()) ?? false);
    return matchesAction && matchesUser && matchesSearch;
  });

  // Stats
  const todayCount = auditLogs.filter((log) => {
    const logDate = new Date(log.timestamp);
    const today = new Date();
    return logDate.toDateString() === today.toDateString();
  }).length;

  const loginCount = auditLogs.filter((log) => log.action === 'LOGIN').length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Audit Logs</h1>
          <p className="text-label-tertiary mt-1">
            Track all user actions and system changes for accountability
          </p>
        </div>
        {user?.role === 'admin' && (
          <button
            onClick={() => {
              if (confirm('Export audit logs to CSV?')) {
                const csv = [
                  ['Timestamp', 'User', 'Action', 'Target', 'Details'].join(','),
                  ...auditLogs.map((log) =>
                    [
                      log.timestamp,
                      log.user,
                      log.action,
                      `"${log.target}"`,
                      `"${log.details || ''}"`,
                    ].join(',')
                  ),
                ].join('\n');

                const blob = new Blob([csv], { type: 'text/csv' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `audit-logs-${new Date().toISOString().split('T')[0]}.csv`;
                a.click();
              }
            }}
            className="btn-secondary flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Export CSV
          </button>
        )}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Total Logs</p>
          <p className="text-2xl font-semibold mt-1">{auditLogs.length}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Today</p>
          <p className="text-2xl font-semibold mt-1">{todayCount}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Login Events</p>
          <p className="text-2xl font-semibold text-health-healthy mt-1">{loginCount}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Unique Users</p>
          <p className="text-2xl font-semibold mt-1">{users.length}</p>
        </GlassCard>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-4">
        {/* Search */}
        <div className="relative flex-1 max-w-md">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-label-tertiary"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            placeholder="Search logs..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-white/50 border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary focus:border-transparent"
          />
        </div>

        {/* Action filter */}
        <select
          value={filterAction}
          onChange={(e) => setFilterAction(e.target.value)}
          className="px-4 py-2 bg-white/50 border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary"
        >
          <option value="all">All Actions</option>
          {actions.map((action) => (
            <option key={action} value={action}>
              {action}
            </option>
          ))}
        </select>

        {/* User filter */}
        <select
          value={filterUser}
          onChange={(e) => setFilterUser(e.target.value)}
          className="px-4 py-2 bg-white/50 border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary"
        >
          <option value="all">All Users</option>
          {users.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
      </div>

      {/* Logs Table */}
      <GlassCard>
        {filteredLogs.length === 0 ? (
          <div className="text-center py-12">
            <svg className="w-12 h-12 text-label-tertiary mx-auto mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p className="text-label-secondary">No audit logs found</p>
            <p className="text-label-tertiary text-sm mt-1">
              {auditLogs.length === 0
                ? 'Actions will be logged here as you use the system'
                : 'Try adjusting your filters'}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-separator-light">
                  <th className="text-left text-xs font-semibold text-label-tertiary uppercase tracking-wide py-3 px-4">
                    Timestamp
                  </th>
                  <th className="text-left text-xs font-semibold text-label-tertiary uppercase tracking-wide py-3 px-4">
                    User
                  </th>
                  <th className="text-left text-xs font-semibold text-label-tertiary uppercase tracking-wide py-3 px-4">
                    Action
                  </th>
                  <th className="text-left text-xs font-semibold text-label-tertiary uppercase tracking-wide py-3 px-4">
                    Target
                  </th>
                  <th className="text-left text-xs font-semibold text-label-tertiary uppercase tracking-wide py-3 px-4">
                    Details
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredLogs.map((log) => (
                  <tr
                    key={log.id}
                    className="border-b border-separator-light last:border-b-0 hover:bg-separator-light/50 transition-colors"
                  >
                    <td className="py-3 px-4">
                      <span className="text-sm text-label-secondary font-mono">
                        {formatDateTime(log.timestamp)}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      <span className="text-sm font-medium text-label-primary">{log.user}</span>
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`inline-flex px-2 py-1 text-xs font-medium rounded ${getActionColor(
                          log.action
                        )}`}
                      >
                        {log.action}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      <span className="text-sm text-label-primary">{log.target}</span>
                    </td>
                    <td className="py-3 px-4">
                      <span className="text-sm text-label-tertiary">{log.details || '-'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {/* Info */}
      <GlassCard padding="sm">
        <p className="text-xs text-label-tertiary text-center">
          Audit logs are stored locally and retained for accountability. Export to CSV for permanent records.
        </p>
      </GlassCard>
    </div>
  );
};

export default AuditLogs;

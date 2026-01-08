import React from 'react';
import { LevelBadge } from '../shared';
import type { RunbookDetail as RunbookDetailType, RunbookExecution } from '../../types';

interface RunbookDetailProps {
  runbook: RunbookDetailType;
  executions: RunbookExecution[];
  isLoadingExecutions?: boolean;
  onClose: () => void;
}

const formatTime = (ms: number): string => {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
};

const formatDate = (dateStr: string): string => {
  const date = new Date(dateStr);
  return date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
};

const formatDateTime = (dateStr: string): string => {
  const date = new Date(dateStr);
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

export const RunbookDetail: React.FC<RunbookDetailProps> = ({
  runbook,
  executions,
  isLoadingExecutions,
  onClose,
}) => {
  const successColor = runbook.success_rate >= 95
    ? 'text-health-healthy'
    : runbook.success_rate >= 80
      ? 'text-health-warning'
      : 'text-health-critical';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-2xl max-h-[90vh] overflow-hidden bg-white/95 backdrop-blur-xl rounded-ios-lg shadow-2xl border border-white/50">
        {/* Header */}
        <div className="p-6 border-b border-separator-light">
          <div className="flex items-start justify-between">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-mono text-label-tertiary">{runbook.id}</span>
                <LevelBadge level={runbook.level} showLabel />
                {runbook.is_disruptive && (
                  <span className="px-2 py-0.5 text-xs bg-orange-100 text-orange-700 rounded-full">
                    Disruptive
                  </span>
                )}
              </div>
              <h2 className="text-xl font-semibold text-label-primary">{runbook.name}</h2>
              <p className="text-label-secondary mt-1">{runbook.description}</p>
            </div>
            <button
              onClick={onClose}
              className="p-2 hover:bg-separator-light rounded-ios-sm transition-colors"
            >
              <svg className="w-5 h-5 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* HIPAA Controls */}
          <div className="flex flex-wrap gap-1 mt-3">
            {runbook.hipaa_controls.map((control) => (
              <span
                key={control}
                className="px-2 py-1 text-xs bg-accent-tint text-accent-primary rounded-full"
              >
                HIPAA {control}
              </span>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="overflow-y-auto max-h-[calc(90vh-200px)]">
          {/* Stats Row */}
          <div className="grid grid-cols-3 gap-4 p-6 bg-gray-50">
            <div className="text-center">
              <p className="text-xs text-gray-600 uppercase tracking-wide">Executions</p>
              <p className="text-2xl font-semibold text-gray-900 mt-1">
                {runbook.execution_count.toLocaleString()}
              </p>
            </div>
            <div className="text-center">
              <p className="text-xs text-gray-600 uppercase tracking-wide">Success Rate</p>
              <p className={`text-2xl font-semibold mt-1 ${successColor}`}>
                {runbook.success_rate.toFixed(1)}%
              </p>
            </div>
            <div className="text-center">
              <p className="text-xs text-gray-600 uppercase tracking-wide">Avg Time</p>
              <p className="text-2xl font-semibold text-gray-900 mt-1">
                {formatTime(runbook.avg_execution_time_ms)}
              </p>
            </div>
          </div>

          {/* Steps */}
          <div className="p-6 border-b border-gray-200">
            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
              Execution Steps
            </h3>
            <div className="space-y-2">
              {runbook.steps.map((step, index) => {
                const s = step as { action?: string; name?: string; timeout_seconds?: number };
                return (
                  <div
                    key={index}
                    className="flex items-center gap-3 p-3 bg-white border border-gray-200 rounded-ios-sm"
                  >
                    <span className="w-6 h-6 flex items-center justify-center bg-accent-primary text-white text-xs font-medium rounded-full">
                      {index + 1}
                    </span>
                    <div className="flex-1">
                      <p className="text-sm font-medium text-gray-900">
                        {s.action || s.name || `Step ${index + 1}`}
                      </p>
                      {s.timeout_seconds && (
                        <p className="text-xs text-gray-500">
                          Timeout: {s.timeout_seconds}s
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Parameters */}
          {Object.keys(runbook.parameters).length > 0 && (
            <div className="p-6 border-b border-gray-200">
              <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
                Parameters
              </h3>
              <div className="grid grid-cols-2 gap-2">
                {Object.entries(runbook.parameters).map(([key, value]) => (
                  <div key={key} className="p-2 bg-gray-50 rounded-ios-sm">
                    <p className="text-xs text-gray-600">{key}</p>
                    <p className="text-sm font-medium text-gray-900">
                      {typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value)}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent Executions */}
          <div className="p-6">
            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
              Recent Executions
            </h3>
            {isLoadingExecutions ? (
              <div className="space-y-2">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="h-16 bg-gray-100 rounded-ios-sm animate-pulse" />
                ))}
              </div>
            ) : executions.length === 0 ? (
              <p className="text-gray-500 text-sm">No recent executions</p>
            ) : (
              <div className="space-y-2">
                {executions.map((exec) => (
                  <div
                    key={exec.id}
                    className="flex items-center gap-3 p-3 bg-white border border-gray-200 rounded-ios-sm"
                  >
                    {/* Status */}
                    <div className={`w-2 h-2 rounded-full flex-shrink-0 ${
                      exec.success ? 'bg-health-healthy' : 'bg-health-critical'
                    }`} />

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium text-gray-900 truncate">
                          {exec.hostname}
                        </p>
                        <span className="text-xs text-gray-500">
                          {exec.site_id.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                        </span>
                      </div>
                      {exec.output && (
                        <p className="text-xs text-gray-600 truncate">{exec.output}</p>
                      )}
                      {exec.error && (
                        <p className="text-xs text-health-critical truncate">{exec.error}</p>
                      )}
                    </div>

                    {/* Time */}
                    <div className="text-right flex-shrink-0">
                      <p className="text-xs text-gray-500">
                        {formatDateTime(exec.executed_at)}
                      </p>
                      <p className="text-xs text-gray-600">
                        {formatTime(exec.execution_time_ms)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="p-6 bg-gray-50 border-t border-gray-200">
            <div className="flex items-center justify-between text-xs text-gray-500">
              <span>Created: {formatDate(runbook.created_at)}</span>
              <span>Last updated: {formatDate(runbook.updated_at)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default RunbookDetail;

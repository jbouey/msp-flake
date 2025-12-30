import React, { useState } from 'react';
import { GlassCard, Spinner } from '../components/shared';
import { RunbookCard, RunbookDetail } from '../components/runbooks';
import { useRunbooks, useRunbook, useRunbookExecutions } from '../hooks';
import type { ResolutionLevel } from '../types';

type FilterLevel = ResolutionLevel | 'all';

export const Runbooks: React.FC = () => {
  const [selectedRunbookId, setSelectedRunbookId] = useState<string | null>(null);
  const [filterLevel, setFilterLevel] = useState<FilterLevel>('all');
  const [searchQuery, setSearchQuery] = useState('');

  // Fetch data
  const { data: runbooks = [], isLoading, error } = useRunbooks();
  const { data: selectedRunbook } = useRunbook(selectedRunbookId);
  const { data: executions = [], isLoading: isLoadingExecutions } = useRunbookExecutions(
    selectedRunbookId,
    10
  );

  // Filter runbooks
  const filteredRunbooks = runbooks.filter((rb) => {
    const matchesLevel = filterLevel === 'all' || rb.level === filterLevel;
    const matchesSearch =
      searchQuery === '' ||
      rb.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      rb.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      rb.hipaa_controls.some((c) => c.toLowerCase().includes(searchQuery.toLowerCase()));
    return matchesLevel && matchesSearch;
  });

  // Stats
  const totalExecutions = runbooks.reduce((sum, rb) => sum + rb.execution_count, 0);
  const avgSuccessRate =
    runbooks.length > 0
      ? runbooks.reduce((sum, rb) => sum + rb.success_rate, 0) / runbooks.length
      : 0;
  const l1Count = runbooks.filter((rb) => rb.level === 'L1').length;
  const disruptiveCount = runbooks.filter((rb) => rb.is_disruptive).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Runbook Library</h1>
          <p className="text-label-tertiary mt-1">
            Automated remediation playbooks with HIPAA control mappings
          </p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Total Runbooks</p>
          <p className="text-2xl font-semibold mt-1">{runbooks.length}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Total Executions</p>
          <p className="text-2xl font-semibold mt-1">{totalExecutions.toLocaleString()}</p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Avg Success Rate</p>
          <p className="text-2xl font-semibold text-health-healthy mt-1">
            {avgSuccessRate.toFixed(1)}%
          </p>
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">L1 Deterministic</p>
          <p className="text-2xl font-semibold text-ios-blue mt-1">{l1Count}</p>
        </GlassCard>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4">
        {/* Search */}
        <div className="relative flex-1 max-w-md">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-label-tertiary"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            type="text"
            placeholder="Search runbooks, HIPAA controls..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-white/50 border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary focus:border-transparent"
          />
        </div>

        {/* Level filter */}
        <div className="flex items-center gap-1 bg-separator-light rounded-ios-md p-1">
          {(['all', 'L1', 'L2', 'L3'] as const).map((level) => (
            <button
              key={level}
              onClick={() => setFilterLevel(level)}
              className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                filterLevel === level
                  ? 'bg-white shadow-sm text-label-primary font-medium'
                  : 'text-label-secondary hover:text-label-primary'
              }`}
            >
              {level === 'all' ? 'All' : level}
            </button>
          ))}
        </div>
      </div>

      {/* Runbook Grid */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Spinner size="lg" />
        </div>
      ) : error ? (
        <GlassCard>
          <div className="text-center py-8">
            <p className="text-health-critical">Failed to load runbooks</p>
            <p className="text-label-tertiary text-sm mt-1">{String(error)}</p>
          </div>
        </GlassCard>
      ) : filteredRunbooks.length === 0 ? (
        <GlassCard>
          <div className="text-center py-8">
            <p className="text-label-secondary">No runbooks match your filters</p>
            <button
              onClick={() => {
                setFilterLevel('all');
                setSearchQuery('');
              }}
              className="text-accent-primary text-sm mt-2 hover:underline"
            >
              Clear filters
            </button>
          </div>
        </GlassCard>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filteredRunbooks.map((runbook) => (
            <RunbookCard
              key={runbook.id}
              runbook={runbook}
              onClick={() => setSelectedRunbookId(runbook.id)}
            />
          ))}
        </div>
      )}

      {/* Legend */}
      <GlassCard padding="sm">
        <div className="flex items-center justify-center gap-6 text-xs text-label-tertiary">
          <div className="flex items-center gap-2">
            <span className="px-1.5 py-0.5 bg-ios-blue/10 text-ios-blue rounded">L1</span>
            <span>Deterministic (&lt;100ms)</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="px-1.5 py-0.5 bg-ios-purple/10 text-ios-purple rounded">L2</span>
            <span>LLM-assisted (2-5s)</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="px-1.5 py-0.5 bg-orange-100 text-orange-700 rounded">Disruptive</span>
            <span>{disruptiveCount} runbooks may cause service interruption</span>
          </div>
        </div>
      </GlassCard>

      {/* Detail Modal */}
      {selectedRunbook && (
        <RunbookDetail
          runbook={selectedRunbook}
          executions={executions}
          isLoadingExecutions={isLoadingExecutions}
          onClose={() => setSelectedRunbookId(null)}
        />
      )}
    </div>
  );
};

export default Runbooks;

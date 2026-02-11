import React, { useState } from 'react';
import { GlassCard, Spinner } from '../components/shared';
import {
  useFrameworkSyncStatus,
  useFrameworkControls,
  useFrameworkCategories,
  useCoverageAnalysis,
  useTriggerFrameworkSync,
  useSyncFramework,
} from '../hooks';
import type { FrameworkSyncStatus } from '../types';

const SOURCE_TYPE_BADGE: Record<string, string> = {
  oscal: 'bg-blue-500/20 text-blue-400',
  manual: 'bg-fill-tertiary text-label-tertiary',
  csv: 'bg-purple-500/20 text-purple-400',
  api: 'bg-green-500/20 text-green-400',
};

function formatDate(iso: string | null): string {
  if (!iso) return 'Never';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function CoverageBar({ pct }: { pct: number }) {
  const color = pct >= 80 ? 'bg-health-healthy' : pct >= 50 ? 'bg-health-warning' : 'bg-health-critical';
  return (
    <div className="w-full h-2 bg-fill-quaternary rounded-full overflow-hidden">
      <div className={`h-full ${color} rounded-full transition-all duration-500`} style={{ width: `${Math.min(pct, 100)}%` }} />
    </div>
  );
}

// Framework card component
function FrameworkCard({
  fw,
  isSelected,
  onSelect,
  onSync,
  syncing,
}: {
  fw: FrameworkSyncStatus;
  isSelected: boolean;
  onSelect: () => void;
  onSync: () => void;
  syncing: boolean;
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left transition-all duration-200 ${isSelected ? 'ring-2 ring-accent-primary' : ''}`}
    >
      <GlassCard>
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-label-primary truncate">{fw.display_name}</h3>
              <div className="flex items-center gap-2 mt-1">
                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${SOURCE_TYPE_BADGE[fw.source_type] || SOURCE_TYPE_BADGE.manual}`}>
                  {fw.source_type.toUpperCase()}
                </span>
                {fw.version && (
                  <span className="text-[11px] text-label-tertiary">{fw.version}</span>
                )}
              </div>
            </div>
            {fw.source_type === 'oscal' && (
              <button
                onClick={(e) => { e.stopPropagation(); onSync(); }}
                disabled={syncing}
                className="text-[10px] font-medium px-2 py-1 rounded-ios-sm bg-accent-tint text-accent-primary hover:bg-accent-primary hover:text-white transition-colors disabled:opacity-50"
              >
                {syncing ? 'Syncing...' : 'Sync'}
              </button>
            )}
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs text-label-tertiary">{fw.our_coverage}/{fw.total_controls} controls</span>
              <span className="text-xs font-medium text-label-secondary">{fw.coverage_pct}%</span>
            </div>
            <CoverageBar pct={fw.coverage_pct} />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-label-quaternary">
              Synced: {formatDate(fw.last_sync)}
            </span>
            {fw.sync_status && (
              <span className={`text-[10px] font-medium ${
                fw.sync_status === 'success' || fw.sync_status === 'seeded'
                  ? 'text-health-healthy'
                  : fw.sync_status === 'failed'
                  ? 'text-health-critical'
                  : 'text-label-tertiary'
              }`}>
                {fw.sync_status}
              </span>
            )}
          </div>
        </div>
      </GlassCard>
    </button>
  );
}

// Coverage matrix component
function CoverageMatrix({
  coverage,
}: {
  coverage: { frameworks: Array<{ framework: string; display_name: string; total_controls: number; our_coverage: number; coverage_pct: number; unmapped_controls: number }>; check_matrix: Record<string, Record<string, string[]>> };
}) {
  const checks = Object.keys(coverage.check_matrix);
  const frameworks = coverage.frameworks;

  if (checks.length === 0 || frameworks.length === 0) {
    return (
      <div className="text-center text-label-tertiary py-8 text-sm">
        No coverage data yet. Trigger a sync to populate.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-separator-light">
            <th className="text-left py-2 px-3 text-label-tertiary font-medium sticky left-0 bg-background-primary z-10">Check</th>
            {frameworks.map((fw) => (
              <th key={fw.framework} className="text-center py-2 px-2 text-label-tertiary font-medium whitespace-nowrap">
                <div className="max-w-[80px] truncate" title={fw.display_name}>
                  {fw.display_name.length > 12 ? fw.framework.toUpperCase().replace(/_/g, ' ') : fw.display_name}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {checks.map((check) => (
            <tr key={check} className="border-b border-separator-light/50 hover:bg-fill-quaternary/50">
              <td className="py-2 px-3 font-mono text-label-secondary sticky left-0 bg-background-primary z-10">{check}</td>
              {frameworks.map((fw) => {
                const controls = coverage.check_matrix[check]?.[fw.framework] || [];
                const mapped = controls.length > 0;
                return (
                  <td key={fw.framework} className="text-center py-2 px-2">
                    {mapped ? (
                      <span className="inline-block w-5 h-5 rounded-full bg-health-healthy/20 text-health-healthy leading-5 text-[10px] font-bold" title={controls.join(', ')}>
                        {controls.length}
                      </span>
                    ) : (
                      <span className="inline-block w-5 h-5 rounded-full bg-fill-quaternary text-label-quaternary leading-5">-</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Control explorer component
function ControlExplorer({ framework }: { framework: string }) {
  const [search, setSearch] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('');

  const { data: categories = [] } = useFrameworkCategories(framework);
  const { data: controls = [], isLoading } = useFrameworkControls(framework, {
    category: selectedCategory || undefined,
    search: search || undefined,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            placeholder="Search controls..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-sm rounded-ios-md bg-fill-quaternary text-label-primary placeholder:text-label-quaternary border border-separator-light focus:border-accent-primary focus:ring-1 focus:ring-accent-primary/30 outline-none transition-colors"
          />
        </div>
        <select
          value={selectedCategory}
          onChange={(e) => setSelectedCategory(e.target.value)}
          className="px-3 py-2 text-sm rounded-ios-md bg-fill-quaternary text-label-primary border border-separator-light focus:border-accent-primary outline-none"
        >
          <option value="">All Categories</option>
          {categories.map((cat) => (
            <option key={cat.category} value={cat.category}>
              {cat.category} ({cat.count})
            </option>
          ))}
        </select>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8"><Spinner size="md" /></div>
      ) : controls.length === 0 ? (
        <div className="text-center text-label-tertiary py-8 text-sm">
          No controls found. {framework === 'nist_800_53' ? 'Trigger a sync to pull OSCAL data.' : 'Seed from YAML to populate.'}
        </div>
      ) : (
        <div className="space-y-1 max-h-[500px] overflow-y-auto">
          {controls.map((ctrl) => (
            <div
              key={ctrl.control_id}
              className="flex items-start gap-3 p-3 rounded-ios-md hover:bg-fill-quaternary/50 transition-colors border border-transparent hover:border-separator-light"
            >
              <div className="flex-shrink-0 w-24">
                <span className="text-xs font-mono font-semibold text-accent-primary">{ctrl.control_id}</span>
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-label-primary font-medium">{ctrl.control_name || 'Unnamed'}</div>
                {ctrl.description && (
                  <div className="text-xs text-label-tertiary mt-0.5 line-clamp-2">{ctrl.description}</div>
                )}
                {ctrl.category && (
                  <span className="inline-block text-[10px] text-label-quaternary bg-fill-quaternary px-1.5 py-0.5 rounded mt-1">
                    {ctrl.category}
                  </span>
                )}
              </div>
              <div className="flex-shrink-0">
                {ctrl.mapped_check ? (
                  <span className="text-[10px] font-medium px-2 py-1 rounded-full bg-health-healthy/20 text-health-healthy whitespace-nowrap">
                    {ctrl.mapped_check}
                  </span>
                ) : (
                  <span className="text-[10px] font-medium px-2 py-1 rounded-full bg-fill-quaternary text-label-quaternary">
                    Gap
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Main page component
const ComplianceLibrary: React.FC = () => {
  const [selectedFramework, setSelectedFramework] = useState<string | null>(null);
  const [view, setView] = useState<'frameworks' | 'coverage'>('frameworks');

  const { data: frameworks = [], isLoading } = useFrameworkSyncStatus();
  const { data: coverage } = useCoverageAnalysis();
  const syncAllMutation = useTriggerFrameworkSync();
  const syncOneMutation = useSyncFramework();

  const selectedFw = frameworks.find((f) => f.framework === selectedFramework);

  // Summary stats
  const totalControls = frameworks.reduce((sum, f) => sum + f.total_controls, 0);
  const totalCovered = frameworks.reduce((sum, f) => sum + f.our_coverage, 0);
  const oscalCount = frameworks.filter((f) => f.source_type === 'oscal').length;
  const avgCoverage = frameworks.length > 0
    ? Math.round(frameworks.reduce((sum, f) => sum + f.coverage_pct, 0) / frameworks.length)
    : 0;

  return (
    <div className="space-y-6 page-enter">
      {/* Summary row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-label-primary">{frameworks.length}</div>
            <div className="text-xs text-label-tertiary mt-1">Frameworks</div>
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-accent-primary">{totalControls.toLocaleString()}</div>
            <div className="text-xs text-label-tertiary mt-1">Total Controls</div>
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-health-healthy">{totalCovered}</div>
            <div className="text-xs text-label-tertiary mt-1">Mapped</div>
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-health-warning">{avgCoverage}%</div>
            <div className="text-xs text-label-tertiary mt-1">Avg Coverage</div>
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-blue-400">{oscalCount}</div>
            <div className="text-xs text-label-tertiary mt-1">Auto-Sync (OSCAL)</div>
          </div>
        </GlassCard>
      </div>

      {/* Actions bar */}
      <div className="flex items-center justify-between">
        <div className="flex gap-2">
          <button
            onClick={() => setView('frameworks')}
            className={`text-sm font-medium px-4 py-2 rounded-ios-md transition-colors ${
              view === 'frameworks'
                ? 'bg-accent-tint text-accent-primary'
                : 'text-label-secondary hover:bg-fill-quaternary'
            }`}
          >
            Frameworks
          </button>
          <button
            onClick={() => setView('coverage')}
            className={`text-sm font-medium px-4 py-2 rounded-ios-md transition-colors ${
              view === 'coverage'
                ? 'bg-accent-tint text-accent-primary'
                : 'text-label-secondary hover:bg-fill-quaternary'
            }`}
          >
            Coverage Matrix
          </button>
        </div>
        <button
          onClick={() => syncAllMutation.mutate()}
          disabled={syncAllMutation.isPending}
          className="text-sm font-medium px-4 py-2 rounded-ios-md bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
        >
          {syncAllMutation.isPending ? 'Syncing All...' : 'Sync All'}
        </button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner size="lg" /></div>
      ) : view === 'frameworks' ? (
        <>
          {/* Framework cards grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {frameworks.map((fw) => (
              <FrameworkCard
                key={fw.framework}
                fw={fw}
                isSelected={selectedFramework === fw.framework}
                onSelect={() => setSelectedFramework(selectedFramework === fw.framework ? null : fw.framework)}
                onSync={() => syncOneMutation.mutate(fw.framework)}
                syncing={syncOneMutation.isPending}
              />
            ))}
          </div>

          {/* Control explorer - shown when a framework is selected */}
          {selectedFw && (
            <GlassCard>
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-lg font-semibold text-label-primary">{selectedFw.display_name} Controls</h2>
                    <p className="text-xs text-label-tertiary mt-0.5">
                      {selectedFw.total_controls} controls | {selectedFw.our_coverage} mapped | {selectedFw.coverage_pct}% coverage
                    </p>
                  </div>
                  <button
                    onClick={() => setSelectedFramework(null)}
                    className="p-1.5 hover:bg-fill-tertiary rounded-ios-sm transition-colors"
                  >
                    <svg className="w-5 h-5 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
                <ControlExplorer framework={selectedFw.framework} />
              </div>
            </GlassCard>
          )}
        </>
      ) : (
        /* Coverage Matrix view */
        <GlassCard>
          <div className="space-y-4">
            <div>
              <h2 className="text-lg font-semibold text-label-primary">Check-to-Framework Coverage</h2>
              <p className="text-xs text-label-tertiary mt-0.5">
                Which infrastructure checks map to which framework controls
              </p>
            </div>
            {coverage ? (
              <CoverageMatrix coverage={coverage} />
            ) : (
              <div className="text-center text-label-tertiary py-8 text-sm">
                Loading coverage data...
              </div>
            )}
          </div>
        </GlassCard>
      )}
    </div>
  );
};

export default ComplianceLibrary;

import React, { useState, useCallback, useMemo } from 'react';
import { GlassCard, Spinner } from '../components/shared';
import {
  useL1Rules,
  useIncidentTypes,
  useRunbooks,
  useCreateL1Rule,
  useDeleteL1Rule,
  useEnableL1Rule,
  useDisableL1Rule,
  useTestL1Rule,
} from '../hooks';
import type { L1Rule, RuleTestResult } from '../utils/api';

/**
 * L1 Rule Builder - create incident -> runbook mappings
 *
 * Allows operators to manually create L1 deterministic rules
 * without using the CLI. These rules map incident types to
 * runbooks for automatic resolution.
 */
export const RuleBuilder: React.FC = () => {
  const [showForm, setShowForm] = useState(false);
  const [incidentType, setIncidentType] = useState('');
  const [customIncidentType, setCustomIncidentType] = useState('');
  const [runbookId, setRunbookId] = useState('');
  const [confidence, setConfidence] = useState(0.9);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [testResults, setTestResults] = useState<RuleTestResult | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // Data fetching
  const { data: rules = [], isLoading: isLoadingRules, isError: isRulesError } = useL1Rules();
  const { data: incidentTypes = [], isLoading: isLoadingTypes } = useIncidentTypes();
  const { data: runbooks = [], isLoading: isLoadingRunbooks } = useRunbooks();

  // Mutations
  const createMutation = useCreateL1Rule();
  const deleteMutation = useDeleteL1Rule();
  const enableMutation = useEnableL1Rule();
  const disableMutation = useDisableL1Rule();
  const testMutation = useTestL1Rule();

  // Stats
  const stats = useMemo(() => {
    const total = rules.length;
    const active = rules.filter(r => r.enabled).length;
    const manual = rules.filter(r => r.source === 'manual').length;
    const promoted = rules.filter(r => r.source === 'promoted').length;
    return { total, active, manual, promoted };
  }, [rules]);

  // Available sources for filter
  const sources = useMemo(() => {
    const s = new Set(rules.map(r => r.source));
    return ['all', ...Array.from(s).sort()];
  }, [rules]);

  // Filtered rules
  const filteredRules = useMemo(() => {
    return rules.filter(rule => {
      const matchesSearch = searchQuery === '' ||
        rule.rule_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
        rule.runbook_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (rule.runbook_name || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        JSON.stringify(rule.incident_pattern).toLowerCase().includes(searchQuery.toLowerCase());
      const matchesSource = sourceFilter === 'all' || rule.source === sourceFilter;
      return matchesSearch && matchesSource;
    });
  }, [rules, searchQuery, sourceFilter]);

  const showFeedback = useCallback((type: 'success' | 'error', message: string) => {
    setFeedback({ type, message });
    setTimeout(() => setFeedback(null), 4000);
  }, []);

  const effectiveIncidentType = customIncidentType || incidentType;

  const handleTest = useCallback(async () => {
    if (!effectiveIncidentType) return;
    try {
      const result = await testMutation.mutateAsync(effectiveIncidentType);
      setTestResults(result);
    } catch (error) {
      showFeedback('error', `Test failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  }, [effectiveIncidentType, testMutation, showFeedback]);

  const handleCreate = useCallback(async () => {
    if (!effectiveIncidentType || !runbookId) {
      showFeedback('error', 'Incident type and runbook are required');
      return;
    }
    try {
      const result = await createMutation.mutateAsync({
        incident_type: effectiveIncidentType,
        runbook_id: runbookId,
        confidence,
      });
      showFeedback('success', `Rule created: ${result.rule_id}`);
      setIncidentType('');
      setCustomIncidentType('');
      setRunbookId('');
      setConfidence(0.9);
      setTestResults(null);
      setShowForm(false);
    } catch (error) {
      showFeedback('error', `Failed to create rule: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  }, [effectiveIncidentType, runbookId, confidence, createMutation, showFeedback]);

  const handleToggle = useCallback(async (rule: L1Rule) => {
    try {
      if (rule.enabled) {
        await disableMutation.mutateAsync(rule.rule_id);
        showFeedback('success', `Rule ${rule.rule_id} disabled`);
      } else {
        await enableMutation.mutateAsync(rule.rule_id);
        showFeedback('success', `Rule ${rule.rule_id} enabled`);
      }
    } catch (error) {
      showFeedback('error', `Failed to toggle rule: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  }, [enableMutation, disableMutation, showFeedback]);

  const handleDelete = useCallback(async (ruleId: string) => {
    try {
      await deleteMutation.mutateAsync(ruleId);
      showFeedback('success', `Rule ${ruleId} deleted`);
      setConfirmDelete(null);
    } catch (error) {
      showFeedback('error', `Failed to delete: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  }, [deleteMutation, showFeedback]);

  const getIncidentTypeFromPattern = (pattern: Record<string, unknown>): string => {
    if (pattern.incident_type) return String(pattern.incident_type);
    // Friendlier fallback: try to extract a meaningful key, otherwise "Custom pattern"
    const keys = Object.keys(pattern);
    if (keys.length === 1) return `${keys[0]}: ${String(pattern[keys[0]])}`;
    return 'Custom pattern';
  };

  const getSourceBadge = (source: string) => {
    const styles: Record<string, string> = {
      manual: 'bg-ios-blue/10 text-ios-blue',
      promoted: 'bg-level-l2/10 text-level-l2',
      builtin: 'bg-label-tertiary/10 text-label-tertiary',
      synced: 'bg-health-healthy/10 text-health-healthy',
      custom: 'bg-ios-purple/10 text-ios-purple',
      protection_profile: 'bg-health-warning/10 text-health-warning',
    };
    return styles[source] || 'bg-label-tertiary/10 text-label-tertiary';
  };

  return (
    <div className="space-y-6">
      {/* Feedback banner */}
      {feedback && (
        <div className={`px-4 py-3 rounded-lg text-sm font-medium ${
          feedback.type === 'success'
            ? 'bg-health-healthy/10 text-health-healthy border border-health-healthy/20'
            : 'bg-health-critical/10 text-health-critical border border-health-critical/20'
        }`}>
          {feedback.message}
        </div>
      )}

      {/* Error state */}
      {isRulesError && (
        <div className="bg-health-critical/10 border border-health-critical/20 px-4 py-3 rounded-lg">
          <p className="text-sm text-health-critical font-medium">
            Failed to load rules. Check your connection and try refreshing.
          </p>
        </div>
      )}

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Total Rules</p>
          {isLoadingRules ? (
            <div className="h-8 animate-pulse bg-separator-light rounded mt-1" />
          ) : (
            <p className="text-2xl font-semibold mt-1">{stats.total}</p>
          )}
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Active</p>
          {isLoadingRules ? (
            <div className="h-8 animate-pulse bg-separator-light rounded mt-1" />
          ) : (
            <p className="text-2xl font-semibold text-health-healthy mt-1">{stats.active}</p>
          )}
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Manual</p>
          {isLoadingRules ? (
            <div className="h-8 animate-pulse bg-separator-light rounded mt-1" />
          ) : (
            <p className="text-2xl font-semibold text-ios-blue mt-1">{stats.manual}</p>
          )}
        </GlassCard>
        <GlassCard padding="md">
          <p className="text-xs text-label-tertiary uppercase tracking-wide">Promoted</p>
          {isLoadingRules ? (
            <div className="h-8 animate-pulse bg-separator-light rounded mt-1" />
          ) : (
            <p className="text-2xl font-semibold text-level-l2 mt-1">{stats.promoted}</p>
          )}
        </GlassCard>
      </div>

      {/* Create Rule form (collapsible) */}
      <GlassCard>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center justify-between w-full"
        >
          <div>
            <h2 className="text-lg font-semibold text-label-primary">Create Rule</h2>
            <p className="text-sm text-label-tertiary mt-0.5">
              Map an incident type to a runbook for automatic L1 resolution
            </p>
          </div>
          <svg
            className={`w-5 h-5 text-label-tertiary transition-transform ${showForm ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {showForm && (
          <div className="mt-6 space-y-4">
            {/* Incident Type */}
            <div>
              <label className="block text-sm font-medium text-label-primary mb-1.5">
                Incident Type
              </label>
              <select
                value={incidentType}
                onChange={e => {
                  setIncidentType(e.target.value);
                  if (e.target.value) setCustomIncidentType('');
                }}
                className="w-full px-3 py-2 bg-fill-secondary border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary"
              >
                <option value="">Select from recent incidents...</option>
                {isLoadingTypes ? (
                  <option disabled>Loading...</option>
                ) : (
                  incidentTypes.map(it => (
                    <option key={it.type} value={it.type}>
                      {it.type} ({it.count} in 30d)
                    </option>
                  ))
                )}
              </select>
              <div className="mt-2">
                <input
                  type="text"
                  placeholder="Or enter a custom incident type..."
                  value={customIncidentType}
                  onChange={e => {
                    setCustomIncidentType(e.target.value);
                    if (e.target.value) setIncidentType('');
                  }}
                  className="w-full px-3 py-2 bg-fill-secondary border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary"
                />
              </div>
            </div>

            {/* Runbook */}
            <div>
              <label className="block text-sm font-medium text-label-primary mb-1.5">
                Runbook
              </label>
              <select
                value={runbookId}
                onChange={e => setRunbookId(e.target.value)}
                className="w-full px-3 py-2 bg-fill-secondary border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary"
              >
                <option value="">Select a runbook...</option>
                {isLoadingRunbooks ? (
                  <option disabled>Loading...</option>
                ) : (
                  runbooks.map(rb => (
                    <option key={rb.id} value={rb.id}>
                      {rb.id} - {rb.name} ({rb.level})
                    </option>
                  ))
                )}
              </select>
            </div>

            {/* Confidence slider */}
            <div>
              <label className="block text-sm font-medium text-label-primary mb-1.5">
                Confidence: {(confidence * 100).toFixed(0)}%
              </label>
              <input
                type="range"
                min="0.5"
                max="1.0"
                step="0.05"
                value={confidence}
                onChange={e => setConfidence(parseFloat(e.target.value))}
                className="w-full accent-accent-primary"
              />
              <div className="flex justify-between text-xs text-label-tertiary mt-1">
                <span>50%</span>
                <span>75%</span>
                <span>100%</span>
              </div>
            </div>

            {/* Actions */}
            <div className="flex items-center gap-3 pt-2">
              <button
                onClick={handleTest}
                disabled={!effectiveIncidentType || testMutation.isPending}
                className="px-4 py-2 text-sm font-medium rounded-ios-md border border-separator-light text-label-primary hover:bg-fill-secondary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {testMutation.isPending ? 'Testing...' : 'Test Rule'}
              </button>
              <button
                onClick={handleCreate}
                disabled={!effectiveIncidentType || !runbookId || createMutation.isPending}
                className="btn-primary text-sm disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {createMutation.isPending ? 'Creating...' : 'Create Rule'}
              </button>
            </div>

            {/* Test Results */}
            {testResults && (
              <div className="mt-4 p-4 bg-fill-secondary rounded-ios-md border border-separator-light">
                <h3 className="text-sm font-semibold text-label-primary mb-2">
                  Test Results: {testResults.count} matches in 7 days
                </h3>
                {testResults.matches.length === 0 ? (
                  <p className="text-sm text-label-tertiary">
                    No matching incidents found in the last 7 days.
                  </p>
                ) : (
                  <div className="space-y-2 max-h-64 overflow-y-auto">
                    {testResults.matches.map(m => (
                      <div key={m.id} className="flex items-center justify-between text-sm py-1.5 border-b border-separator-light last:border-0">
                        <div className="flex items-center gap-3">
                          <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                            m.severity === 'critical' ? 'bg-health-critical/10 text-health-critical' :
                            m.severity === 'high' ? 'bg-health-warning/10 text-health-warning' :
                            'bg-label-tertiary/10 text-label-tertiary'
                          }`}>
                            {m.severity || '-'}
                          </span>
                          <span className="text-label-primary">{m.incident_type || '-'}</span>
                          <span className="text-label-tertiary text-xs">{m.hostname || '-'}</span>
                        </div>
                        <span className="text-xs text-label-tertiary">
                          {new Date(m.created_at).toLocaleDateString()}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </GlassCard>

      {/* Filters */}
      <div className="flex items-center gap-4">
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
            placeholder="Search rules, runbooks..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-fill-secondary border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary focus:border-transparent"
          />
        </div>

        <div className="flex items-center gap-1 bg-separator-light rounded-ios-md p-1">
          {sources.map(source => (
            <button
              key={source}
              onClick={() => setSourceFilter(source)}
              className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                sourceFilter === source
                  ? 'bg-background-secondary shadow-sm text-label-primary font-medium'
                  : 'text-label-secondary hover:text-label-primary'
              }`}
            >
              {source === 'all' ? 'All' : source}
            </button>
          ))}
        </div>
      </div>

      {/* Rules Table */}
      {isLoadingRules ? (
        <div className="flex items-center justify-center py-12">
          <Spinner size="lg" />
        </div>
      ) : filteredRules.length === 0 ? (
        <GlassCard>
          <div className="text-center py-8">
            <p className="text-label-secondary">
              {rules.length === 0 ? 'No L1 rules configured yet.' : 'No rules match your filters.'}
            </p>
            {rules.length === 0 && (
              <button
                onClick={() => setShowForm(true)}
                className="text-accent-primary text-sm mt-2 hover:underline"
              >
                Create your first rule
              </button>
            )}
            {rules.length > 0 && searchQuery && (
              <button
                onClick={() => { setSearchQuery(''); setSourceFilter('all'); }}
                className="text-accent-primary text-sm mt-2 hover:underline"
              >
                Clear filters
              </button>
            )}
          </div>
        </GlassCard>
      ) : (
        <GlassCard padding="none">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-separator-light">
                  <th className="text-left py-3 px-4 text-xs text-label-tertiary uppercase tracking-wide font-semibold">Enabled</th>
                  <th className="text-left py-3 px-4 text-xs text-label-tertiary uppercase tracking-wide font-semibold">Rule ID</th>
                  <th className="text-left py-3 px-4 text-xs text-label-tertiary uppercase tracking-wide font-semibold">Incident Type</th>
                  <th className="text-left py-3 px-4 text-xs text-label-tertiary uppercase tracking-wide font-semibold">Runbook</th>
                  <th className="text-left py-3 px-4 text-xs text-label-tertiary uppercase tracking-wide font-semibold">Confidence</th>
                  <th className="text-left py-3 px-4 text-xs text-label-tertiary uppercase tracking-wide font-semibold">Source</th>
                  <th className="text-right py-3 px-4 text-xs text-label-tertiary uppercase tracking-wide font-semibold">Matches</th>
                  <th className="text-right py-3 px-4 text-xs text-label-tertiary uppercase tracking-wide font-semibold">Success</th>
                  <th className="text-right py-3 px-4 text-xs text-label-tertiary uppercase tracking-wide font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredRules.map(rule => (
                  <tr
                    key={rule.rule_id}
                    className={`border-b border-separator-light last:border-0 transition-colors hover:bg-fill-quaternary ${
                      !rule.enabled ? 'opacity-60' : ''
                    }`}
                  >
                    {/* Toggle */}
                    <td className="py-3 px-4">
                      <button
                        onClick={() => handleToggle(rule)}
                        className={`relative w-10 h-5 rounded-full transition-colors ${
                          rule.enabled ? 'bg-health-healthy' : 'bg-separator-medium'
                        }`}
                        title={rule.enabled ? 'Disable rule' : 'Enable rule'}
                      >
                        <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                          rule.enabled ? 'translate-x-5' : 'translate-x-0'
                        }`} />
                      </button>
                    </td>

                    {/* Rule ID */}
                    <td className="py-3 px-4">
                      <span className="font-mono text-xs text-label-tertiary">{rule.rule_id}</span>
                    </td>

                    {/* Incident Type */}
                    <td className="py-3 px-4">
                      <span className="text-label-primary">
                        {getIncidentTypeFromPattern(rule.incident_pattern)}
                      </span>
                    </td>

                    {/* Runbook */}
                    <td className="py-3 px-4">
                      <div>
                        <span className="text-label-primary">{rule.runbook_name || rule.runbook_id}</span>
                        {rule.runbook_name && (
                          <span className="block text-xs text-label-tertiary">{rule.runbook_id}</span>
                        )}
                      </div>
                    </td>

                    {/* Confidence */}
                    <td className="py-3 px-4">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                        rule.confidence >= 0.9 ? 'bg-health-healthy/10 text-health-healthy' :
                        rule.confidence >= 0.7 ? 'bg-health-warning/10 text-health-warning' :
                        'bg-label-tertiary/10 text-label-tertiary'
                      }`}>
                        {(rule.confidence * 100).toFixed(0)}%
                      </span>
                    </td>

                    {/* Source */}
                    <td className="py-3 px-4">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded ${getSourceBadge(rule.source)}`}>
                        {rule.source}
                      </span>
                    </td>

                    {/* Matches */}
                    <td className="py-3 px-4 text-right tabular-nums">
                      {rule.match_count?.toLocaleString() ?? 0}
                    </td>

                    {/* Success Rate */}
                    <td className="py-3 px-4 text-right">
                      <span className={`tabular-nums ${
                        (rule.success_rate ?? 0) >= 90 ? 'text-health-healthy' :
                        (rule.success_rate ?? 0) >= 70 ? 'text-health-warning' :
                        'text-health-critical'
                      }`}>
                        {rule.success_rate !== null && rule.success_rate !== undefined ? `${rule.success_rate.toFixed(1)}%` : '--'}
                      </span>
                    </td>

                    {/* Actions */}
                    <td className="py-3 px-4 text-right">
                      {confirmDelete === rule.rule_id ? (
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => handleDelete(rule.rule_id)}
                            className="text-xs text-health-critical font-medium hover:underline"
                          >
                            Confirm
                          </button>
                          <button
                            onClick={() => setConfirmDelete(null)}
                            className="text-xs text-label-tertiary hover:underline"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setConfirmDelete(rule.rule_id)}
                          className="p-1.5 hover:bg-health-critical/10 rounded-ios-sm transition-colors"
                          title="Delete rule"
                        >
                          <svg className="w-4 h-4 text-label-tertiary hover:text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {/* Info section */}
      <GlassCard padding="md">
        <h3 className="font-semibold text-label-primary mb-2">How L1 Rules Work</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm text-label-secondary">
          <div className="flex gap-3">
            <div className="w-8 h-8 flex-shrink-0 rounded-full bg-ios-blue/20 flex items-center justify-center">
              <span className="text-ios-blue font-bold text-xs">1</span>
            </div>
            <div>
              <p className="font-medium text-label-primary">Incident Arrives</p>
              <p className="text-xs">When the daemon reports an incident, L1 rules are evaluated first</p>
            </div>
          </div>
          <div className="flex gap-3">
            <div className="w-8 h-8 flex-shrink-0 rounded-full bg-level-l1/20 flex items-center justify-center">
              <span className="text-level-l1 font-bold text-xs">2</span>
            </div>
            <div>
              <p className="font-medium text-label-primary">Pattern Match</p>
              <p className="text-xs">If the incident type matches, the mapped runbook is dispatched instantly</p>
            </div>
          </div>
          <div className="flex gap-3">
            <div className="w-8 h-8 flex-shrink-0 rounded-full bg-health-healthy/20 flex items-center justify-center">
              <span className="text-health-healthy font-bold text-xs">3</span>
            </div>
            <div>
              <p className="font-medium text-label-primary">Auto-Resolve</p>
              <p className="text-xs">Deterministic resolution in &lt;100ms with $0 LLM cost</p>
            </div>
          </div>
        </div>
      </GlassCard>
    </div>
  );
};

export default RuleBuilder;

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Spinner, LevelBadge, useToast } from '../components/shared';
import { StatusBadge } from '../components/composed';
import { IncidentRow } from '../components/incidents/IncidentRow';
import { useIncidents, useSites, useResolveIncident, useEscalateIncident, useSuppressIncident } from '../hooks';
import { incidentApi } from '../utils/api';
import type { Incident, IncidentDetail } from '../types';
import { CHECK_TYPE_LABELS } from '../types';
import { CATEGORY_LABELS, TIER_LABELS, formatTimeAgo } from '../constants';

// Category -> check_types mapping (matches backend compliance-health endpoint)
const CATEGORY_CHECK_TYPES: Record<string, string[]> = {
  patching: ['nixos_generation', 'windows_update', 'linux_patching',
             'linux_unattended_upgrades', 'linux_kernel_params'],
  antivirus: ['windows_defender', 'windows_defender_exclusions', 'defender_exclusions'],
  backup: ['backup_status', 'windows_backup_status'],
  logging: ['audit_logging', 'windows_audit_policy', 'linux_audit', 'linux_logging',
            'security_audit', 'audit_policy', 'linux_log_forwarding'],
  firewall: ['firewall', 'windows_firewall_status', 'firewall_status', 'linux_firewall',
             'network_profile', 'net_unexpected_ports'],
  encryption: ['bitlocker', 'windows_bitlocker_status', 'linux_crypto', 'windows_smb_signing',
               'bitlocker_status', 'smb_signing', 'smb1_protocol'],
  access_control: ['rogue_admin_users', 'linux_accounts', 'windows_password_policy',
                   'linux_permissions', 'linux_ssh_config', 'windows_screen_lock_policy',
                   'screen_lock', 'screen_lock_policy', 'password_policy',
                   'guest_account', 'rdp_nla', 'rogue_scheduled_tasks'],
  services: ['critical_services', 'linux_services', 'windows_service_dns',
            'windows_service_netlogon', 'windows_service_spooler',
            'windows_service_w32time', 'windows_service_wuauserv', 'agent_status',
            'service_dns', 'service_netlogon', 'service_status',
            'spooler_service', 'linux_failed_services', 'ntp_sync',
            'winrm', 'dns_config', 'net_dns_resolution',
            'net_expected_service', 'net_host_reachability'],
};

/** Severity -> left border color */
const SEVERITY_BORDER: Record<string, string> = {
  critical: 'border-health-critical',
  high: 'border-ios-orange',
  medium: 'border-health-warning',
  low: 'border-ios-blue',
};

/** Tier label suitable for KPI card display */
const TIER_SHORT_LABELS: Record<string, string> = {
  L1: 'L1',
  L2: 'L2',
  L3: 'L3',
};

// ---- SVG icon components (inline, no emoji) ----
const CheckCircleIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
  </svg>
);

const ChevronDownIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
  </svg>
);

const ShieldCheckIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
  </svg>
);

const ExclamationTriangleIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
  </svg>
);

/**
 * Expanded incident detail panel
 */
const IncidentDetailPanel: React.FC<{ incidentId: string; onClose: () => void }> = ({ incidentId, onClose }) => {
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    incidentApi.getIncident(incidentId)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [incidentId]);

  if (loading) {
    return (
      <div className="p-6 border-t border-separator-light bg-fill-primary rounded-b-ios-md">
        <div className="flex items-center gap-2 text-label-tertiary">
          <Spinner size="sm" /> Loading incident details...
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="p-6 border-t border-separator-light bg-fill-primary rounded-b-ios-md">
        <p className="text-label-tertiary">Failed to load incident details.</p>
      </div>
    );
  }

  const checkLabel = CHECK_TYPE_LABELS[detail.check_type] || detail.check_type;
  const driftData = detail.drift_data || {};
  const hasDriftInfo = Object.keys(driftData).length > 0;

  return (
    <div className="p-6 border-t border-separator-light bg-fill-primary rounded-b-ios-md space-y-4">
      {/* Header with close */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-label-primary">{checkLabel}</h3>
        <button onClick={onClose} className="text-label-tertiary hover:text-label-primary text-sm">
          Close
        </button>
      </div>

      {/* Key info grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div>
          <span className="text-label-tertiary">Hostname:</span>
          <div className="text-label-primary font-medium">{detail.hostname || 'Unknown'}</div>
        </div>
        <div>
          <span className="text-label-tertiary">Site:</span>
          <div className="text-label-primary font-medium">{detail.site_id}</div>
        </div>
        <div>
          <span className="text-label-tertiary">Severity:</span>
          <div className="text-label-primary font-medium capitalize">{detail.severity}</div>
        </div>
        <div>
          <span className="text-label-tertiary">Resolution:</span>
          <div>{detail.resolution_level ? <LevelBadge level={detail.resolution_level} showLabel /> : <span className="text-health-warning">Pending</span>}</div>
        </div>
      </div>

      {/* HIPAA Controls */}
      {detail.hipaa_controls.length > 0 && (
        <div>
          <span className="text-xs text-label-tertiary">HIPAA Controls:</span>
          <div className="flex flex-wrap gap-1 mt-1">
            {detail.hipaa_controls.map(ctrl => (
              <span key={ctrl} className="px-2 py-0.5 bg-accent-primary/10 text-accent-primary rounded text-xs font-mono">{ctrl}</span>
            ))}
          </div>
        </div>
      )}

      {/* Drift details */}
      {hasDriftInfo && (
        <div className="rounded-lg bg-glass-bg/30 p-4">
          <h4 className="text-xs font-medium text-label-tertiary uppercase mb-2">Drift Details</h4>
          <div className="space-y-2 text-sm">
            {'message' in driftData && driftData.message !== undefined && driftData.message !== null && (
              <p className="text-label-primary">{String(driftData.message)}</p>
            )}
            {'expected' in driftData && driftData.expected !== undefined && driftData.expected !== null && (
              <div className="flex gap-2">
                <span className="text-label-tertiary">Expected:</span>
                <span className="text-health-healthy font-mono text-xs">{String(driftData.expected)}</span>
              </div>
            )}
            {'actual' in driftData && driftData.actual !== undefined && driftData.actual !== null && (
              <div className="flex gap-2">
                <span className="text-label-tertiary">Actual:</span>
                <span className="text-health-critical font-mono text-xs">{String(driftData.actual)}</span>
              </div>
            )}
            {'platform' in driftData && driftData.platform !== undefined && driftData.platform !== null && (
              <div className="flex gap-2">
                <span className="text-label-tertiary">Platform:</span>
                <span className="text-label-primary">{String(driftData.platform)}</span>
              </div>
            )}
            {'source' in driftData && driftData.source !== undefined && driftData.source !== null && (
              <div className="flex gap-2">
                <span className="text-label-tertiary">Source:</span>
                <span className="text-label-primary">{String(driftData.source)}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Healing/Runbook info */}
      {(detail.runbook_executed || detail.execution_log) && (
        <div className="rounded-lg bg-health-healthy/5 border border-health-healthy/20 p-4">
          <h4 className="text-xs font-medium text-label-tertiary uppercase mb-2">Auto-Healing</h4>
          {detail.runbook_executed && (
            <div className="text-sm">
              <span className="text-label-tertiary">Runbook:</span>{' '}
              <span className="text-label-primary font-mono">{detail.runbook_executed}</span>
            </div>
          )}
          {detail.execution_log && (
            <pre className="mt-2 text-xs text-label-secondary bg-glass-bg/50 rounded p-2 overflow-x-auto max-h-32">
              {detail.execution_log}
            </pre>
          )}
        </div>
      )}

      {/* Remediation state */}
      {(detail.remediation_attempts > 0 || detail.remediation_exhausted) && (
        <div className={`rounded-lg p-4 ${detail.remediation_exhausted ? 'bg-health-critical/5 border border-health-critical/20' : 'bg-ios-orange/5 border border-ios-orange/20'}`}>
          <div className="flex items-center gap-2 mb-2">
            <h4 className="text-xs font-medium text-label-tertiary uppercase">Remediation State</h4>
            {detail.remediation_exhausted && (
              <span className="px-1.5 py-0.5 text-[10px] font-semibold bg-health-critical/10 text-health-critical rounded">
                Budget Exhausted
              </span>
            )}
          </div>
          <div className="text-sm text-label-secondary mb-2">
            {detail.remediation_attempts} attempt{detail.remediation_attempts !== 1 ? 's' : ''} total
            {detail.remediation_exhausted && ' — manual intervention required'}
          </div>
          {detail.remediation_history && detail.remediation_history.length > 0 && (
            <div className="space-y-1.5">
              {detail.remediation_history.map((entry, idx) => (
                <div key={idx} className="flex items-center gap-2 text-xs">
                  <span className={`w-6 text-center font-mono font-semibold ${
                    entry.tier === 'L1' ? 'text-accent-primary' : entry.tier === 'L2' ? 'text-ios-orange' : 'text-health-critical'
                  }`}>{entry.tier}</span>
                  <span className={`px-1.5 py-0.5 rounded ${
                    entry.result === 'order_created' ? 'bg-health-healthy/10 text-health-healthy' : 'bg-label-tertiary/10 text-label-tertiary'
                  }`}>{entry.result}</span>
                  {entry.runbook_id && (
                    <span className="text-label-tertiary font-mono">{entry.runbook_id}</span>
                  )}
                  {entry.confidence !== undefined && entry.confidence !== null && (
                    <span className="text-label-tertiary">{(entry.confidence * 100).toFixed(0)}%</span>
                  )}
                  <span className="text-label-tertiary ml-auto">{new Date(entry.timestamp).toLocaleTimeString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Status and timestamps */}
      <div className="flex items-center gap-4 text-xs text-label-tertiary pt-2 border-t border-separator-light">
        <span>Created: {new Date(detail.created_at).toLocaleString()}</span>
        {detail.resolved_at && (
          <span>Resolved: {new Date(detail.resolved_at).toLocaleString()}</span>
        )}
        <StatusBadge status={detail.resolved ? 'resolved' : 'resolving'} showDot={false} />
      </div>
    </div>
  );
};

export const Incidents: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlSiteId = searchParams.get('site_id') || '';
  const urlCategory = searchParams.get('category') || '';
  const urlHostname = searchParams.get('hostname') || '';

  const [selectedSiteId, setSelectedSiteId] = useState<string>(urlSiteId);
  const [selectedLevel, setSelectedLevel] = useState<string>('');
  const [selectedCategory, setSelectedCategory] = useState<string>(urlCategory);
  const [selectedHostname, setSelectedHostname] = useState<string>(urlHostname);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);
  const [showRecent, setShowRecent] = useState(false);
  const [page, setPage] = useState(0);
  const limit = 50;

  const { addToast } = useToast();

  // Mutations for incident actions
  const resolveMutation = useResolveIncident();
  const escalateMutation = useEscalateIncident();
  const suppressMutation = useSuppressIncident();

  // Sync URL params on mount
  useEffect(() => {
    if (urlSiteId) setSelectedSiteId(urlSiteId);
    if (urlCategory) setSelectedCategory(urlCategory);
    if (urlHostname) setSelectedHostname(urlHostname);
  }, [urlSiteId, urlCategory, urlHostname]);

  // Fetch sites for the selector
  const { data: sitesData } = useSites({ limit: 200, sort_by: 'clinic_name', sort_dir: 'asc' });
  const sites = sitesData?.sites || [];

  // Fetch all incidents (no resolved filter -- we split client-side)
  const { data: rawIncidents = [], isLoading, error } = useIncidents({
    site_id: selectedSiteId || undefined,
    limit: 200,
    offset: 0,
    level: selectedLevel || undefined,
  });

  // Client-side category + hostname filter (applied to all sections)
  const allFiltered = useMemo(() => rawIncidents.filter((i: Incident) => {
    if (selectedCategory && CATEGORY_CHECK_TYPES[selectedCategory]) {
      if (!CATEGORY_CHECK_TYPES[selectedCategory].includes(i.check_type)) return false;
    }
    if (selectedHostname) {
      if ((i.hostname || '').toLowerCase() !== selectedHostname.toLowerCase()) return false;
    }
    return true;
  }), [rawIncidents, selectedCategory, selectedHostname]);

  // ---- Section data splits ----
  const activeIncidents = useMemo(() =>
    allFiltered.filter((i: Incident) => !i.resolved),
    [allFiltered]
  );

  const recentResolved = useMemo(() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return allFiltered
      .filter((i: Incident) => i.resolved && new Date(i.resolved_at || i.created_at).getTime() > cutoff)
      .slice(0, 20);
  }, [allFiltered]);

  const recentL1 = recentResolved.filter((i: Incident) => i.resolution_level === 'L1').length;
  const recentL2 = recentResolved.filter((i: Incident) => i.resolution_level === 'L2').length;
  const recentL3 = recentResolved.filter((i: Incident) => i.resolution_level === 'L3').length;

  // History section: paginated resolved incidents for the table
  const historyIncidents = useMemo(() => {
    const resolved = allFiltered.filter((i: Incident) => i.resolved);
    const start = page * limit;
    return resolved.slice(start, start + limit);
  }, [allFiltered, page, limit]);

  const totalResolved = useMemo(() =>
    allFiltered.filter((i: Incident) => i.resolved).length,
    [allFiltered]
  );

  const totalIncidents = allFiltered.length;
  const hasMore = historyIncidents.length === limit;

  // Reset page when filters change
  useEffect(() => { setPage(0); }, [selectedSiteId, selectedLevel, selectedCategory, selectedHostname]);

  // Update URL when category/site changes
  const handleCategoryChange = (cat: string) => {
    setSelectedCategory(cat);
    const params = new URLSearchParams(searchParams);
    if (cat) params.set('category', cat); else params.delete('category');
    if (selectedSiteId) params.set('site_id', selectedSiteId); else params.delete('site_id');
    setSearchParams(params, { replace: true });
  };

  const handleSiteChange = (siteId: string) => {
    setSelectedSiteId(siteId);
    const params = new URLSearchParams(searchParams);
    if (siteId) params.set('site_id', siteId); else params.delete('site_id');
    if (selectedCategory) params.set('category', selectedCategory);
    setSearchParams(params, { replace: true });
  };

  // ---- Incident action handlers ----
  const handleResolve = useCallback((id: string) => {
    setActionLoadingId(id);
    resolveMutation.mutate(id, {
      onSuccess: () => {
        addToast('success', 'Incident resolved');
        setActionLoadingId(null);
      },
      onError: (err) => {
        addToast('error', err instanceof Error ? err.message : 'Failed to resolve incident');
        setActionLoadingId(null);
      },
    });
  }, [resolveMutation, addToast]);

  const handleEscalate = useCallback((id: string) => {
    setActionLoadingId(id);
    escalateMutation.mutate({ id }, {
      onSuccess: () => {
        addToast('warning', 'Escalated to L3');
        setActionLoadingId(null);
      },
      onError: (err) => {
        addToast('error', err instanceof Error ? err.message : 'Failed to escalate incident');
        setActionLoadingId(null);
      },
    });
  }, [escalateMutation, addToast]);

  const handleSuppress = useCallback((id: string) => {
    setActionLoadingId(id);
    suppressMutation.mutate(id, {
      onSuccess: () => {
        addToast('info', 'Suppressed for 24h');
        setActionLoadingId(null);
      },
      onError: (err) => {
        addToast('error', err instanceof Error ? err.message : 'Failed to suppress incident');
        setActionLoadingId(null);
      },
    });
  }, [suppressMutation, addToast]);

  // Loading / error states
  if (error) {
    return (
      <div className="space-y-6 page-enter">
        <div className="text-center py-12">
          <ExclamationTriangleIcon className="w-12 h-12 mx-auto text-health-critical mb-3" />
          <p className="text-health-critical font-medium">{error.message}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5 page-enter">
      {/* ============================================================= */}
      {/* Page header                                                    */}
      {/* ============================================================= */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-label-primary tracking-tight">Incidents</h1>
          <p className="text-sm text-label-tertiary mt-0.5">
            MSP triage view
            {selectedSiteId ? ` — ${sites.find(s => s.site_id === selectedSiteId)?.clinic_name || selectedSiteId}` : ''}
            {selectedHostname ? ` on ${selectedHostname}` : ''}
          </p>
        </div>
      </div>

      {/* ============================================================= */}
      {/* KPI Summary Cards                                              */}
      {/* ============================================================= */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="glass-card p-3 text-center">
          <p className={`text-2xl font-bold ${activeIncidents.length > 0 ? 'text-health-critical' : 'text-label-primary'}`}>
            {isLoading ? '-' : activeIncidents.length}
          </p>
          <p className="text-[10px] text-label-tertiary uppercase tracking-wider mt-0.5">Active</p>
        </div>
        <div className="glass-card p-3 text-center">
          <p className="text-2xl font-bold text-health-healthy">
            {isLoading ? '-' : recentResolved.length}
          </p>
          <p className="text-[10px] text-label-tertiary uppercase tracking-wider mt-0.5">Resolved 24h</p>
        </div>
        <div className="glass-card p-3 text-center">
          <p className="text-2xl font-bold text-ios-blue">
            {isLoading ? '-' : recentL1}
          </p>
          <p className="text-[10px] text-label-tertiary uppercase tracking-wider mt-0.5">Auto-Healed</p>
        </div>
        <div className="glass-card p-3 text-center">
          <p className="text-2xl font-bold text-label-primary">
            {isLoading ? '-' : totalIncidents}
          </p>
          <p className="text-[10px] text-label-tertiary uppercase tracking-wider mt-0.5">Total</p>
        </div>
      </div>

      {/* ============================================================= */}
      {/* Global filter bar (applies to all sections)                    */}
      {/* ============================================================= */}
      <div className="glass-card p-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
          {/* Site selector */}
          <div className="flex items-center gap-2 flex-1 max-w-md">
            <svg className="w-4 h-4 text-label-tertiary shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
            </svg>
            <select
              value={selectedSiteId}
              onChange={e => handleSiteChange(e.target.value)}
              className="flex-1 px-3 py-2 text-sm border border-separator-light rounded-ios bg-fill-primary focus:ring-2 focus:ring-accent-primary focus:border-transparent"
            >
              <option value="">All Sites</option>
              {sites.map(site => (
                <option key={site.site_id} value={site.site_id}>
                  {site.clinic_name} ({site.site_id})
                </option>
              ))}
            </select>
          </div>

          {/* Category filter */}
          <div className="flex gap-1 flex-wrap">
            <button
              onClick={() => handleCategoryChange('')}
              className={`px-2.5 py-1.5 text-xs rounded-ios-sm transition-colors ${
                !selectedCategory
                  ? 'bg-accent-primary text-white'
                  : 'bg-separator-light text-label-secondary hover:bg-separator-light/80'
              }`}
            >
              All Categories
            </button>
            {Object.entries(CATEGORY_LABELS).map(([key, label]) => (
              <button
                key={key}
                onClick={() => handleCategoryChange(key)}
                className={`px-2.5 py-1.5 text-xs rounded-ios-sm transition-colors ${
                  selectedCategory === key
                    ? 'bg-accent-primary text-white'
                    : 'bg-separator-light text-label-secondary hover:bg-separator-light/80'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Hostname filter badge */}
          {selectedHostname && (
            <div className="flex items-center gap-1">
              <span className="px-2.5 py-1.5 text-xs rounded-ios-sm bg-accent-primary text-white flex items-center gap-1.5">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
                {selectedHostname}
                <button
                  onClick={() => {
                    setSelectedHostname('');
                    const params = new URLSearchParams(searchParams);
                    params.delete('hostname');
                    setSearchParams(params, { replace: true });
                  }}
                  className="ml-0.5 hover:bg-white/20 rounded-full p-0.5"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </span>
            </div>
          )}

          {/* Level filter */}
          <div className="flex gap-1">
            {[
              { value: '', label: 'All Levels' },
              { value: 'L1', label: 'L1' },
              { value: 'L2', label: 'L2' },
              { value: 'L3', label: 'L3' },
            ].map(option => (
              <button
                key={option.value || 'all-levels'}
                onClick={() => setSelectedLevel(option.value)}
                className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                  selectedLevel === option.value
                    ? 'bg-accent-primary text-white'
                    : 'bg-separator-light text-label-secondary hover:bg-separator-light/80'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="text-center py-12">
          <Spinner size="lg" />
          <p className="text-label-tertiary mt-4">Loading incidents...</p>
        </div>
      )}

      {!isLoading && (
        <>
          {/* ============================================================= */}
          {/* Section 1: Active Threats                                      */}
          {/* ============================================================= */}
          <div>
            <h2 className="text-sm font-semibold text-label-primary mb-3 flex items-center gap-2">
              <ExclamationTriangleIcon className="w-4 h-4 text-health-critical" />
              Active Threats
              {activeIncidents.length > 0 && (
                <span className="px-1.5 py-0.5 text-[10px] font-semibold bg-health-critical/10 text-health-critical rounded-full">
                  {activeIncidents.length}
                </span>
              )}
            </h2>
            <div className="space-y-3">
              {activeIncidents.map((incident: Incident) => (
                <div
                  key={incident.id}
                  className={`glass-card p-4 border-l-4 ${SEVERITY_BORDER[incident.severity] || 'border-health-warning'}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <StatusBadge status="active" />
                        <span className="font-semibold text-label-primary truncate">{incident.hostname || 'Unknown'}</span>
                        <span className="text-label-tertiary hidden sm:inline">
                          {'\u00B7'}
                        </span>
                        <span className="text-sm text-label-secondary truncate">
                          {CHECK_TYPE_LABELS[incident.check_type] || incident.check_type}
                        </span>
                      </div>
                      <p className="text-xs text-label-tertiary mt-1">
                        {formatTimeAgo(incident.created_at)}
                        {' \u00B7 '}
                        <span className="capitalize">{incident.severity}</span>
                        {incident.resolution_level && (
                          <>
                            {' \u00B7 '}
                            {TIER_SHORT_LABELS[incident.resolution_level] || incident.resolution_level}
                            {' \u2014 '}
                            {TIER_LABELS[incident.resolution_level as keyof typeof TIER_LABELS] || ''}
                          </>
                        )}
                      </p>
                      {incident.remediation_attempts > 0 && (
                        <p className="text-xs text-health-warning mt-1">
                          {incident.remediation_attempts} remediation attempt{incident.remediation_attempts !== 1 ? 's' : ''}
                          {incident.remediation_exhausted ? ' -- exhausted, needs manual review' : ''}
                        </p>
                      )}
                      {incident.hipaa_controls.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          {incident.hipaa_controls.slice(0, 3).map(ctrl => (
                            <span key={ctrl} className="px-1.5 py-0.5 bg-accent-primary/10 text-accent-primary rounded text-[10px] font-mono">
                              {ctrl}
                            </span>
                          ))}
                          {incident.hipaa_controls.length > 3 && (
                            <span className="text-[10px] text-label-tertiary">+{incident.hipaa_controls.length - 3}</span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      {actionLoadingId === String(incident.id) ? (
                        <span className="w-20 flex justify-center">
                          <svg className="w-4 h-4 animate-spin text-label-tertiary" viewBox="0 0 24 24" fill="none">
                            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="31.4 31.4" strokeLinecap="round" />
                          </svg>
                        </span>
                      ) : (
                        <>
                          <button
                            onClick={() => handleResolve(String(incident.id))}
                            className="px-2.5 py-1.5 text-xs font-medium rounded-ios-sm bg-health-healthy/10 text-health-healthy hover:bg-health-healthy/20 transition-colors"
                            title="Resolve"
                          >
                            Resolve
                          </button>
                          <button
                            onClick={() => handleEscalate(String(incident.id))}
                            className="px-2.5 py-1.5 text-xs font-medium rounded-ios-sm bg-ios-orange/10 text-ios-orange hover:bg-ios-orange/20 transition-colors"
                            title="Escalate to L3"
                          >
                            Escalate
                          </button>
                          <button
                            onClick={() => handleSuppress(String(incident.id))}
                            className="px-2.5 py-1.5 text-xs font-medium rounded-ios-sm bg-label-tertiary/10 text-label-tertiary hover:bg-label-tertiary/20 transition-colors"
                            title="Suppress 24h"
                          >
                            Suppress
                          </button>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Expandable detail panel */}
                  {expandedId === String(incident.id) && (
                    <IncidentDetailPanel
                      incidentId={String(incident.id)}
                      onClose={() => setExpandedId(null)}
                    />
                  )}

                  {/* Click-to-expand hint */}
                  <button
                    onClick={() => setExpandedId(expandedId === String(incident.id) ? null : String(incident.id))}
                    className="mt-2 text-[10px] text-label-tertiary hover:text-label-secondary transition-colors"
                  >
                    {expandedId === String(incident.id) ? 'Hide details' : 'View details'}
                  </button>
                </div>
              ))}

              {activeIncidents.length === 0 && (
                <div className="glass-card p-6 text-center">
                  <div className="w-10 h-10 rounded-full bg-health-healthy/10 flex items-center justify-center mx-auto mb-3">
                    <ShieldCheckIcon className="w-5 h-5 text-health-healthy" />
                  </div>
                  <p className="text-sm font-medium text-label-primary">All clear</p>
                  <p className="text-xs text-label-tertiary mt-1">No active incidents. All systems monitored and healthy.</p>
                </div>
              )}
            </div>
          </div>

          {/* ============================================================= */}
          {/* Section 2: Recently Resolved (last 24h, collapsed by default) */}
          {/* ============================================================= */}
          <div className="glass-card overflow-hidden">
            <button
              onClick={() => setShowRecent(!showRecent)}
              className="w-full p-4 flex items-center justify-between text-left hover:bg-fill-secondary/50 transition-colors"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-health-healthy/10 flex items-center justify-center flex-shrink-0">
                  <CheckCircleIcon className="w-4 h-4 text-health-healthy" />
                </div>
                <div>
                  <span className="text-sm font-semibold text-label-primary">
                    {recentResolved.length} resolved in last 24h
                  </span>
                  <span className="text-xs text-label-tertiary ml-2">
                    {recentL1} auto-healed
                    {' \u00B7 '}
                    {recentL2} AI-assisted
                    {' \u00B7 '}
                    {recentL3} escalated
                  </span>
                </div>
              </div>
              <ChevronDownIcon className={`w-4 h-4 text-label-tertiary transition-transform flex-shrink-0 ${showRecent ? 'rotate-180' : ''}`} />
            </button>

            {showRecent && (
              <div className="border-t border-separator-light p-4 space-y-2">
                {recentResolved.length > 0 ? (
                  recentResolved.map((incident: Incident) => (
                    <div key={incident.id}>
                      <IncidentRow
                        incident={incident}
                        compact={true}
                        onClick={() => setExpandedId(expandedId === String(incident.id) ? null : String(incident.id))}
                      />
                      {expandedId === String(incident.id) && (
                        <IncidentDetailPanel
                          incidentId={String(incident.id)}
                          onClose={() => setExpandedId(null)}
                        />
                      )}
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-label-tertiary text-center py-4">No incidents resolved in the last 24 hours.</p>
                )}
              </div>
            )}
          </div>

          {/* ============================================================= */}
          {/* Section 3: Incident History (full searchable table)           */}
          {/* ============================================================= */}
          <div className="glass-card overflow-hidden">
            <div className="p-4 border-b border-separator-light flex items-center justify-between">
              <h2 className="text-base font-semibold text-label-primary">Incident History</h2>
              <span className="text-xs text-label-tertiary">{totalResolved} resolved total</span>
            </div>
            <div className="p-4">
              {historyIncidents.length > 0 ? (
                <div className="space-y-2 stagger-list">
                  {historyIncidents.map((incident: Incident) => (
                    <div key={incident.id}>
                      <IncidentRow
                        incident={incident}
                        compact={false}
                        onClick={() => setExpandedId(expandedId === String(incident.id) ? null : String(incident.id))}
                        onResolve={handleResolve}
                        onEscalate={handleEscalate}
                        onSuppress={handleSuppress}
                        actionLoading={actionLoadingId}
                      />
                      {expandedId === String(incident.id) && (
                        <IncidentDetailPanel
                          incidentId={String(incident.id)}
                          onClose={() => setExpandedId(null)}
                        />
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8">
                  <CheckCircleIcon className="w-10 h-10 mx-auto text-label-tertiary/40 mb-2" />
                  <p className="text-sm text-label-secondary">No resolved incidents</p>
                  <p className="text-xs text-label-tertiary mt-1">
                    {selectedSiteId || selectedCategory || selectedHostname ? 'Try adjusting your filters' : 'Incident history will appear here once resolved'}
                  </p>
                </div>
              )}

              {/* Pagination */}
              {historyIncidents.length > 0 && (page > 0 || hasMore) && (
                <div className="flex items-center justify-between pt-4 mt-4 border-t border-separator-light">
                  <p className="text-sm text-label-tertiary">
                    Page {page + 1}{hasMore ? '' : ' (last)'}
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setPage(p => Math.max(0, p - 1))}
                      disabled={page === 0}
                      className="px-3 py-1.5 text-sm rounded-ios bg-fill-tertiary text-label-secondary hover:bg-fill-secondary disabled:opacity-30 disabled:cursor-not-allowed"
                    >
                      Previous
                    </button>
                    <button
                      onClick={() => setPage(p => p + 1)}
                      disabled={!hasMore}
                      className="px-3 py-1.5 text-sm rounded-ios bg-fill-tertiary text-label-secondary hover:bg-fill-secondary disabled:opacity-30 disabled:cursor-not-allowed"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default Incidents;

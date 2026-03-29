import React, { useState, useEffect, useCallback } from 'react';
import { usePartner } from './PartnerContext';
import { STATUS_LABELS } from '../constants';
import { csrfHeaders } from '../utils/csrf';

interface OnboardingSite {
  site_id: string;
  clinic_name: string;
  contact_name: string | null;
  contact_email: string | null;
  stage: string;
  progress_percent: number;
  days_in_stage: number;
  stage_entered_at: string | null;
  blockers: string[];
  notes: string | null;
  appliance_count: number;
  credential_count: number;
  last_checkin: string | null;
  created_at: string | null;
}

interface OnboardingData {
  pipeline: OnboardingSite[];
  completed: OnboardingSite[];
  total: number;
  in_progress_count: number;
  completed_count: number;
}

const STAGE_META: Record<string, { label: string; color: string; icon: string }> = {
  lead:         { label: 'Lead',         color: '#94A3B8', icon: 'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z' },
  discovery:    { label: 'Discovery',    color: '#8B5CF6', icon: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z' },
  proposal:     { label: 'Proposal',     color: '#6366F1', icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' },
  contract:     { label: 'Contract',     color: '#4F46E5', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4' },
  intake:       { label: 'Intake',       color: '#0EA5E9', icon: 'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10' },
  creds:        { label: 'Credentials',  color: '#14B8A6', icon: 'M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z' },
  shipped:      { label: 'Shipped',      color: '#F59E0B', icon: 'M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4' },
  received:     { label: 'Received',     color: '#F97316', icon: 'M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4' },
  connectivity: { label: 'Connectivity', color: '#10B981', icon: 'M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.14 0M1.394 9.393c5.857-5.858 15.355-5.858 21.213 0' },
  scanning:     { label: 'Scanning',     color: '#22C55E', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z' },
  baseline:     { label: 'Baseline',     color: '#16A34A', icon: 'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z' },
  compliant:    { label: STATUS_LABELS.compliant, color: '#059669', icon: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z' },
  active:       { label: 'Active',       color: '#047857', icon: 'M5 13l4 4L19 7' },
};

export const PartnerOnboarding: React.FC = () => {
  const { apiKey, isAuthenticated } = usePartner();
  const [data, setData] = useState<OnboardingData | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggeringCheckin, setTriggeringCheckin] = useState<string | null>(null);
  const [expandedSite, setExpandedSite] = useState<string | null>(null);

  const fetchOptions = useCallback((): RequestInit => {
    return apiKey
      ? { headers: { 'X-API-Key': apiKey } }
      : { credentials: 'include' };
  }, [apiKey]);

  const loadData = useCallback(async () => {
    if (!isAuthenticated) return;
    try {
      const res = await fetch('/api/partners/me/onboarding', fetchOptions());
      if (res.ok) {
        setData(await res.json());
      }
    } catch {
      // Onboarding data load failed — page will show loading state
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated, fetchOptions]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const triggerCheckin = async (siteId: string) => {
    setTriggeringCheckin(siteId);
    try {
      const opts: RequestInit = apiKey
        ? { method: 'POST', headers: { 'X-API-Key': apiKey } }
        : { method: 'POST', credentials: 'include', headers: { ...csrfHeaders() } };

      const res = await fetch(`/api/partners/me/sites/${siteId}/trigger-checkin`, opts);
      if (res.ok) {
        setTimeout(loadData, 3000);
      }
    } catch {
      // Trigger checkin failed silently — non-critical
    } finally {
      setTriggeringCheckin(null);
    }
  };

  const formatRelative = (dateStr: string | null) => {
    if (!dateStr) return 'Never';
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
    return `${Math.floor(mins / 1440)}d ago`;
  };

  const getStageMeta = (stage: string) => STAGE_META[stage] || { label: stage, color: '#94A3B8', icon: 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z' };

  if (loading) {
    return (
      <div className="space-y-4">
        {[1, 2, 3].map(i => (
          <div key={i} className="bg-white rounded-2xl p-6 shadow-sm border border-slate-100 animate-pulse">
            <div className="h-4 bg-slate-200 rounded w-1/3 mb-3" />
            <div className="h-3 bg-slate-100 rounded w-full mb-2" />
            <div className="h-3 bg-slate-100 rounded w-2/3" />
          </div>
        ))}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="bg-white rounded-2xl p-12 shadow-sm border border-slate-100 text-center">
        <p className="text-slate-500">Failed to load onboarding data.</p>
        <button onClick={loadData} className="mt-3 text-indigo-600 hover:text-indigo-800 text-sm font-medium">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Summary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Total Sites</p>
          <p className="text-2xl font-bold text-slate-900 tabular-nums">{data.total}</p>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">In Progress</p>
          <p className="text-2xl font-bold text-amber-600 tabular-nums">{data.in_progress_count}</p>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Completed</p>
          <p className="text-2xl font-bold text-green-600 tabular-nums">{data.completed_count}</p>
        </div>
      </div>

      {/* Pipeline */}
      {data.pipeline.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 uppercase tracking-wide mb-3">Active Pipeline</h3>
          <div className="space-y-3">
            {data.pipeline.map((site) => {
              const meta = getStageMeta(site.stage);
              const isExpanded = expandedSite === site.site_id;

              return (
                <div
                  key={site.site_id}
                  className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden hover:shadow-md transition-shadow"
                >
                  {/* Main row */}
                  <button
                    onClick={() => setExpandedSite(isExpanded ? null : site.site_id)}
                    className="w-full px-5 py-4 flex items-center gap-4 text-left"
                  >
                    {/* Stage icon */}
                    <div
                      className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                      style={{ background: `${meta.color}18` }}
                    >
                      <svg className="w-5 h-5" style={{ color: meta.color }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={meta.icon} />
                      </svg>
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <p className="font-semibold text-slate-900 truncate">{site.clinic_name}</p>
                        <span
                          className="px-2 py-0.5 text-xs font-medium rounded-full text-white flex-shrink-0"
                          style={{ background: meta.color }}
                        >
                          {meta.label}
                        </span>
                        {site.blockers.length > 0 && (
                          <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-red-100 text-red-700 flex-shrink-0">
                            {site.blockers.length} blocker{site.blockers.length > 1 ? 's' : ''}
                          </span>
                        )}
                      </div>

                      {/* Progress bar */}
                      <div className="flex items-center gap-3">
                        <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{ width: `${site.progress_percent}%`, background: meta.color }}
                          />
                        </div>
                        <span className="text-xs font-medium text-slate-500 tabular-nums w-8 text-right">
                          {site.progress_percent}%
                        </span>
                      </div>
                    </div>

                    {/* Days in stage */}
                    <div className="text-right flex-shrink-0">
                      <p className="text-lg font-bold text-slate-900 tabular-nums">{site.days_in_stage}</p>
                      <p className="text-xs text-slate-500">days</p>
                    </div>

                    {/* Chevron */}
                    <svg
                      className={`w-5 h-5 text-slate-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                      fill="none" stroke="currentColor" viewBox="0 0 24 24"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>

                  {/* Expanded detail */}
                  {isExpanded && (
                    <div className="px-5 pb-4 pt-0 border-t border-slate-100">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 py-3">
                        <div>
                          <p className="text-xs text-slate-500">Site ID</p>
                          <p className="text-sm font-mono text-slate-700">{site.site_id}</p>
                        </div>
                        <div>
                          <p className="text-xs text-slate-500">Appliances</p>
                          <p className="text-sm font-medium text-slate-700">{site.appliance_count}</p>
                        </div>
                        <div>
                          <p className="text-xs text-slate-500">Credentials</p>
                          <p className="text-sm font-medium text-slate-700">{site.credential_count}</p>
                        </div>
                        <div>
                          <p className="text-xs text-slate-500">Last Check-in</p>
                          <p className="text-sm font-medium text-slate-700">{formatRelative(site.last_checkin)}</p>
                        </div>
                      </div>

                      {site.contact_name && (
                        <p className="text-sm text-slate-600 mb-2">
                          Contact: {site.contact_name} {site.contact_email ? `(${site.contact_email})` : ''}
                        </p>
                      )}

                      {site.blockers.length > 0 && (
                        <div className="mb-3">
                          <p className="text-xs font-medium text-red-600 uppercase tracking-wide mb-1">Blockers</p>
                          <ul className="space-y-1">
                            {site.blockers.map((b, i) => (
                              <li key={i} className="flex items-start gap-2 text-sm text-red-700">
                                <svg className="w-4 h-4 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                                </svg>
                                {b}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {site.notes && (
                        <p className="text-sm text-slate-500 italic mb-3">{site.notes}</p>
                      )}

                      {/* Actions */}
                      <div className="flex gap-2 pt-2">
                        {site.appliance_count > 0 && (
                          <button
                            onClick={() => triggerCheckin(site.site_id)}
                            disabled={triggeringCheckin === site.site_id}
                            className="px-3 py-1.5 text-xs font-medium bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100 disabled:opacity-50 transition flex items-center gap-1.5"
                          >
                            {triggeringCheckin === site.site_id ? (
                              <>
                                <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                                </svg>
                                Requesting...
                              </>
                            ) : (
                              <>
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                                </svg>
                                Trigger Check-in
                              </>
                            )}
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Empty pipeline */}
      {data.pipeline.length === 0 && data.completed.length === 0 && (
        <div className="bg-white rounded-2xl p-12 shadow-sm border border-slate-100 text-center">
          <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-slate-900 mb-2">No Sites in Pipeline</h3>
          <p className="text-slate-500">Create a provision code from the Provisions tab to start onboarding.</p>
        </div>
      )}

      {/* Completed */}
      {data.completed.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 uppercase tracking-wide mb-3">Completed</h3>
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
            <table className="w-full">
              <thead className="bg-slate-50 border-b">
                <tr>
                  <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Site</th>
                  <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Stage</th>
                  <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Appliances</th>
                  <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Last Check-in</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.completed.map((site) => {
                  const meta = getStageMeta(site.stage);
                  return (
                    <tr key={site.site_id} className="hover:bg-green-50/30">
                      <td className="px-5 py-3">
                        <p className="font-medium text-slate-900">{site.clinic_name}</p>
                        <p className="text-xs text-slate-500">{site.site_id}</p>
                      </td>
                      <td className="px-5 py-3">
                        <span
                          className="px-2 py-0.5 text-xs font-medium rounded-full text-white"
                          style={{ background: meta.color }}
                        >
                          {meta.label}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-sm text-slate-600">{site.appliance_count}</td>
                      <td className="px-5 py-3 text-sm text-slate-600">{formatRelative(site.last_checkin)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

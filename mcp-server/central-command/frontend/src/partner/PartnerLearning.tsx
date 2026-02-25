import React, { useState, useEffect } from 'react';
import { usePartner } from './PartnerContext';

interface LearningStats {
  pending_candidates: number;
  active_promoted_rules: number;
  total_executions_30d: number;
  l1_resolution_rate: number;
  l2_resolution_rate: number;
  l3_escalation_rate: number;
  avg_success_rate: number;
}

interface PromotionCandidate {
  id: string;
  pattern_signature: string;
  site_id: string;
  site_name: string;
  total_occurrences: number;
  l1_resolutions: number;
  l2_resolutions: number;
  l3_resolutions: number;
  success_rate: number;
  avg_resolution_time_ms: number | null;
  recommended_action: string | null;
  first_seen: string | null;
  last_seen: string | null;
  approval_status: string;
  healing_tier: string;
  client_endorsed: boolean;
  client_endorsed_at: string | null;
}

interface PromotedRule {
  id: string;
  rule_id: string;
  pattern_signature: string;
  site_id: string;
  site_name: string | null;
  status: string;
  deployment_count: number;
  promoted_at: string;
  last_deployed_at: string | null;
  notes: string | null;
}

export const PartnerLearning: React.FC = () => {
  const { apiKey, isAuthenticated } = usePartner();

  const [stats, setStats] = useState<LearningStats | null>(null);
  const [candidates, setCandidates] = useState<PromotionCandidate[]>([]);
  const [promotedRules, setPromotedRules] = useState<PromotedRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Modal state
  const [selectedCandidate, setSelectedCandidate] = useState<PromotionCandidate | null>(null);
  const [showApproveModal, setShowApproveModal] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [customName, setCustomName] = useState('');
  const [approvalNotes, setApprovalNotes] = useState('');
  const [rejectReason, setRejectReason] = useState('');
  const [approving, setApproving] = useState(false);

  // Bulk selection state
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkApproving, setBulkApproving] = useState(false);
  const [bulkRejecting, setBulkRejecting] = useState(false);
  const [showBulkRejectModal, setShowBulkRejectModal] = useState(false);
  const [bulkRejectReason, setBulkRejectReason] = useState('');

  // Filter state
  const [filterEndorsed, setFilterEndorsed] = useState(false);

  // View state
  const [showPromotedRules, setShowPromotedRules] = useState(false);

  const fetchOptions: RequestInit = apiKey
    ? { headers: { 'X-API-Key': apiKey } }
    : { credentials: 'include' };

  useEffect(() => {
    if (isAuthenticated) {
      loadData();
    }
  }, [isAuthenticated]);

  const loadData = async () => {
    setLoading(true);
    setError(null);

    try {
      const [statsRes, candidatesRes, rulesRes] = await Promise.all([
        fetch('/api/partners/me/learning/stats', fetchOptions),
        fetch('/api/partners/me/learning/candidates', fetchOptions),
        fetch('/api/partners/me/learning/promoted-rules', fetchOptions),
      ]);

      if (statsRes.ok) {
        const data = await statsRes.json();
        setStats(data);
      }

      if (candidatesRes.ok) {
        const data = await candidatesRes.json();
        setCandidates(data.candidates || []);
      }

      if (rulesRes.ok) {
        const data = await rulesRes.json();
        setPromotedRules(data.rules || []);
      }
    } catch (e) {
      console.error('Failed to load learning data', e);
      setError('Failed to load learning data');
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async () => {
    if (!selectedCandidate) return;
    setApproving(true);
    setError(null);

    try {
      const response = await fetch(`/api/partners/me/learning/candidates/${selectedCandidate.id}/approve`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
        credentials: apiKey ? undefined : 'include',
        body: JSON.stringify({
          deploy_immediately: true,
          custom_name: customName || undefined,
          notes: approvalNotes || undefined,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setSuccess(`Rule ${data.rule_id} created and deployed to ${data.deployed_to} appliances`);
        setShowApproveModal(false);
        setSelectedCandidate(null);
        setCustomName('');
        setApprovalNotes('');
        loadData();
      } else {
        const err = await response.json();
        setError(err.detail || 'Failed to approve candidate');
      }
    } catch (e) {
      setError('Failed to approve candidate');
    } finally {
      setApproving(false);
    }
  };

  const handleReject = async () => {
    if (!selectedCandidate || !rejectReason.trim()) return;
    setApproving(true);
    setError(null);

    try {
      const response = await fetch(`/api/partners/me/learning/candidates/${selectedCandidate.id}/reject`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
        credentials: apiKey ? undefined : 'include',
        body: JSON.stringify({ reason: rejectReason }),
      });

      if (response.ok) {
        setSuccess('Pattern rejected');
        setShowRejectModal(false);
        setSelectedCandidate(null);
        setRejectReason('');
        loadData();
      } else {
        const err = await response.json();
        setError(err.detail || 'Failed to reject candidate');
      }
    } catch (e) {
      setError('Failed to reject candidate');
    } finally {
      setApproving(false);
    }
  };

  const handleToggleRuleStatus = async (rule: PromotedRule) => {
    const newStatus = rule.status === 'active' ? 'disabled' : 'active';

    try {
      const response = await fetch(`/api/partners/me/learning/promoted-rules/${rule.rule_id}/status?status=${newStatus}`, {
        method: 'PATCH',
        headers: apiKey ? { 'X-API-Key': apiKey } : {},
        credentials: apiKey ? undefined : 'include',
      });

      if (response.ok) {
        setSuccess(`Rule ${rule.rule_id} ${newStatus}`);
        loadData();
      } else {
        const err = await response.json();
        setError(err.detail || 'Failed to update rule status');
      }
    } catch (e) {
      setError('Failed to update rule status');
    }
  };

  // Filtered candidates for display
  const actionableCandidates = candidates.filter(c => c.approval_status === 'not_submitted');
  const displayCandidates = filterEndorsed
    ? candidates.filter(c => c.client_endorsed)
    : candidates;

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === actionableCandidates.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(actionableCandidates.map(c => c.id)));
    }
  };

  const handleBulkApprove = async () => {
    if (selectedIds.size === 0) return;
    setBulkApproving(true);
    setError(null);

    try {
      const response = await fetch('/api/partners/me/learning/candidates/bulk-approve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
        credentials: apiKey ? undefined : 'include',
        body: JSON.stringify({
          pattern_ids: Array.from(selectedIds),
          deploy_immediately: true,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setSuccess(`Bulk approved: ${data.approved} rules created${data.failed ? `, ${data.failed} failed` : ''}`);
        setSelectedIds(new Set());
        loadData();
      } else {
        const err = await response.json();
        setError(err.detail || 'Bulk approve failed');
      }
    } catch (e) {
      setError('Bulk approve failed');
    } finally {
      setBulkApproving(false);
    }
  };

  const handleBulkReject = async () => {
    if (selectedIds.size === 0 || !bulkRejectReason.trim()) return;
    setBulkRejecting(true);
    setError(null);

    try {
      const response = await fetch('/api/partners/me/learning/candidates/bulk-reject', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
        credentials: apiKey ? undefined : 'include',
        body: JSON.stringify({
          pattern_ids: Array.from(selectedIds),
          reason: bulkRejectReason,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setSuccess(`Bulk rejected: ${data.rejected} patterns${data.failed ? `, ${data.failed} failed` : ''}`);
        setSelectedIds(new Set());
        setShowBulkRejectModal(false);
        setBulkRejectReason('');
        loadData();
      } else {
        const err = await response.json();
        setError(err.detail || 'Bulk reject failed');
      }
    } catch (e) {
      setError('Bulk reject failed');
    } finally {
      setBulkRejecting(false);
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'approved':
      case 'active':
        return 'bg-green-100 text-green-800';
      case 'pending':
      case 'not_submitted':
        return 'bg-yellow-100 text-yellow-800';
      case 'rejected':
      case 'disabled':
        return 'bg-red-100 text-red-800';
      default:
        return 'bg-slate-100 text-slate-800';
    }
  };

  const formatPercentage = (value: number) => {
    return `${(value * 100).toFixed(1)}%`;
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleDateString();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center p-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
        <span className="ml-3 text-slate-500">Loading learning data...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Alerts */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-500 hover:text-red-700">&times;</button>
        </div>
      )}
      {success && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-lg flex items-center justify-between">
          <span>{success}</span>
          <button onClick={() => setSuccess(null)} className="text-green-500 hover:text-green-700">&times;</button>
        </div>
      )}

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
            <p className="text-sm text-slate-500">Pending Candidates</p>
            <p className="text-2xl font-bold text-yellow-600 tabular-nums">{stats.pending_candidates}</p>
          </div>
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
            <p className="text-sm text-slate-500">Active L1 Rules</p>
            <p className="text-2xl font-bold text-green-600 tabular-nums">{stats.active_promoted_rules}</p>
          </div>
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
            <p className="text-sm text-slate-500">L1 Resolution Rate</p>
            <p className="text-2xl font-bold text-indigo-600 tabular-nums">{formatPercentage(stats.l1_resolution_rate)}</p>
          </div>
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
            <p className="text-sm text-slate-500">Avg Success Rate</p>
            <p className="text-2xl font-bold text-blue-600 tabular-nums">{formatPercentage(stats.avg_success_rate)}</p>
          </div>
        </div>
      )}

      {/* Promotion Candidates */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <div>
            <h3 className="text-lg font-semibold text-slate-900">Promotion Candidates</h3>
            <p className="text-sm text-slate-500">Patterns eligible for L1 promotion (5+ occurrences, 90%+ success rate)</p>
          </div>
          <button
            onClick={() => setFilterEndorsed(!filterEndorsed)}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition ${
              filterEndorsed
                ? 'bg-teal-100 text-teal-700 border border-teal-200'
                : 'bg-slate-100 text-slate-600 border border-slate-200 hover:bg-slate-200'
            }`}
          >
            {filterEndorsed ? 'Client Endorsed' : 'Show All'}
          </button>
        </div>

        {/* Bulk action bar */}
        {selectedIds.size > 0 && (
          <div className="px-6 py-3 bg-indigo-50 border-b border-indigo-100 flex items-center justify-between">
            <span className="text-sm font-medium text-indigo-700">
              {selectedIds.size} selected
            </span>
            <div className="flex gap-2">
              <button
                onClick={handleBulkApprove}
                disabled={bulkApproving}
                className="px-4 py-1.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 disabled:opacity-50 transition"
              >
                {bulkApproving ? 'Approving...' : `Approve Selected (${selectedIds.size})`}
              </button>
              <button
                onClick={() => setShowBulkRejectModal(true)}
                className="px-4 py-1.5 bg-red-100 text-red-700 text-sm font-medium rounded-lg hover:bg-red-200 transition"
              >
                Reject Selected ({selectedIds.size})
              </button>
              <button
                onClick={() => setSelectedIds(new Set())}
                className="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-700"
              >
                Clear
              </button>
            </div>
          </div>
        )}

        {displayCandidates.length === 0 ? (
          <div className="p-12 text-center">
            <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-slate-900 mb-2">No Promotion Candidates</h3>
            <p className="text-slate-500">
              {filterEndorsed
                ? 'No client-endorsed candidates found. Try showing all candidates.'
                : 'Patterns will appear here once they meet the promotion criteria.'}
            </p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-slate-50 border-b">
              <tr>
                <th className="px-3 py-3 w-10">
                  <input
                    type="checkbox"
                    checked={selectedIds.size === actionableCandidates.length && actionableCandidates.length > 0}
                    onChange={toggleSelectAll}
                    className="w-4 h-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                  />
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">Pattern</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">Site</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">Occurrences</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">Success Rate</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {displayCandidates.map((candidate) => (
                <tr key={candidate.id} className={`hover:bg-indigo-50/50 ${selectedIds.has(candidate.id) ? 'bg-indigo-50/30' : ''}`}>
                  <td className="px-3 py-4">
                    {candidate.approval_status === 'not_submitted' && (
                      <input
                        type="checkbox"
                        checked={selectedIds.has(candidate.id)}
                        onChange={() => toggleSelect(candidate.id)}
                        className="w-4 h-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                      />
                    )}
                  </td>
                  <td className="px-4 py-4">
                    <div>
                      <p className="text-sm font-medium text-slate-900">
                        {candidate.recommended_action || 'Unknown Pattern'}
                      </p>
                      <p className="text-xs text-slate-500 font-mono">
                        {candidate.pattern_signature.substring(0, 12)}...
                      </p>
                    </div>
                  </td>
                  <td className="px-4 py-4">
                    <p className="text-sm text-slate-900">{candidate.site_name}</p>
                    <div className="flex gap-1 mt-1">
                      <span className={`inline-flex px-1.5 py-0.5 text-[10px] font-medium rounded ${
                        candidate.healing_tier === 'full_coverage'
                          ? 'bg-indigo-100 text-indigo-700'
                          : 'bg-slate-100 text-slate-500'
                      }`}>
                        {candidate.healing_tier === 'full_coverage' ? 'Full' : 'Std'}
                      </span>
                      {candidate.client_endorsed && (
                        <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] font-medium rounded bg-teal-100 text-teal-700">
                          <svg className="w-2.5 h-2.5" fill="currentColor" viewBox="0 0 20 20">
                            <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                          </svg>
                          Endorsed
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-4">
                    <p className="text-sm text-slate-900">{candidate.total_occurrences}</p>
                    <p className="text-xs text-slate-500">
                      L2: {candidate.l2_resolutions}
                    </p>
                  </td>
                  <td className="px-4 py-4">
                    <span className={`px-2 py-1 text-xs font-medium rounded-full ${
                      candidate.success_rate >= 0.95 ? 'bg-green-100 text-green-800' :
                      candidate.success_rate >= 0.90 ? 'bg-yellow-100 text-yellow-800' :
                      'bg-red-100 text-red-800'
                    }`}>
                      {formatPercentage(candidate.success_rate)}
                    </span>
                  </td>
                  <td className="px-4 py-4">
                    <span className={`px-2 py-1 text-xs font-medium rounded-full ${getStatusColor(candidate.approval_status)}`}>
                      {candidate.approval_status === 'not_submitted' ? 'Pending Review' : candidate.approval_status}
                    </span>
                  </td>
                  <td className="px-4 py-4">
                    {candidate.approval_status === 'not_submitted' && (
                      <div className="flex space-x-2">
                        <button
                          onClick={() => {
                            setSelectedCandidate(candidate);
                            setShowApproveModal(true);
                          }}
                          className="px-3 py-1 bg-green-600 text-white text-sm font-medium rounded hover:bg-green-700 transition"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => {
                            setSelectedCandidate(candidate);
                            setShowRejectModal(true);
                          }}
                          className="px-3 py-1 bg-red-100 text-red-700 text-sm font-medium rounded hover:bg-red-200 transition"
                        >
                          Reject
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Promoted Rules */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
        <div
          className="px-6 py-4 border-b border-slate-200 flex items-center justify-between cursor-pointer"
          onClick={() => setShowPromotedRules(!showPromotedRules)}
        >
          <div>
            <h3 className="text-lg font-semibold text-slate-900">Promoted Rules ({promotedRules.length})</h3>
            <p className="text-sm text-slate-500">Active L1 rules deployed from pattern promotions</p>
          </div>
          <svg
            className={`w-5 h-5 text-slate-500 transform transition ${showPromotedRules ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>

        {showPromotedRules && (
          promotedRules.length === 0 ? (
            <div className="p-8 text-center text-slate-500">
              No promoted rules yet. Approve promotion candidates to create L1 rules.
            </div>
          ) : (
            <table className="w-full">
              <thead className="bg-slate-50 border-b">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Rule ID</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Site</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Deployments</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Promoted</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {promotedRules.map((rule) => (
                  <tr key={rule.id} className="hover:bg-indigo-50/50">
                    <td className="px-6 py-4">
                      <p className="text-sm font-medium text-slate-900 font-mono">{rule.rule_id}</p>
                    </td>
                    <td className="px-6 py-4">
                      <p className="text-sm text-slate-900">{rule.site_name || rule.site_id}</p>
                    </td>
                    <td className="px-6 py-4">
                      <p className="text-sm text-slate-900">{rule.deployment_count}</p>
                    </td>
                    <td className="px-6 py-4">
                      <p className="text-sm text-slate-900">{formatDate(rule.promoted_at)}</p>
                    </td>
                    <td className="px-6 py-4">
                      <span className={`px-2 py-1 text-xs font-medium rounded-full ${getStatusColor(rule.status)}`}>
                        {rule.status}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <button
                        onClick={() => handleToggleRuleStatus(rule)}
                        className={`px-3 py-1 text-sm font-medium rounded transition ${
                          rule.status === 'active'
                            ? 'bg-red-100 text-red-700 hover:bg-red-200'
                            : 'bg-green-100 text-green-700 hover:bg-green-200'
                        }`}
                      >
                        {rule.status === 'active' ? 'Disable' : 'Enable'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}
      </div>

      {/* Approve Modal */}
      {showApproveModal && selectedCandidate && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 modal-backdrop">
          <div className="bg-white rounded-2xl p-6 w-full max-w-lg shadow-xl">
            <h3 className="text-lg font-semibold text-slate-900 mb-4">Approve Pattern for L1 Promotion</h3>

            <div className="space-y-4 mb-6">
              <div className="bg-slate-50 rounded-lg p-4">
                <p className="text-sm text-slate-500">Pattern</p>
                <p className="text-sm font-medium text-slate-900">{selectedCandidate.recommended_action}</p>
                <p className="text-xs text-slate-500 font-mono mt-1">{selectedCandidate.pattern_signature}</p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="bg-slate-50 rounded-lg p-4">
                  <p className="text-sm text-slate-500">Occurrences</p>
                  <p className="text-lg font-semibold text-slate-900">{selectedCandidate.total_occurrences}</p>
                </div>
                <div className="bg-slate-50 rounded-lg p-4">
                  <p className="text-sm text-slate-500">Success Rate</p>
                  <p className="text-lg font-semibold text-green-600">{formatPercentage(selectedCandidate.success_rate)}</p>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Custom Rule Name (optional)</label>
                <input
                  type="text"
                  value={customName}
                  onChange={(e) => setCustomName(e.target.value)}
                  placeholder="e.g., Firewall Auto-Heal"
                  className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Notes (optional)</label>
                <textarea
                  value={approvalNotes}
                  onChange={(e) => setApprovalNotes(e.target.value)}
                  placeholder="Approval notes..."
                  rows={2}
                  className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
              </div>
            </div>

            <div className="flex justify-end space-x-3">
              <button
                onClick={() => {
                  setShowApproveModal(false);
                  setSelectedCandidate(null);
                  setCustomName('');
                  setApprovalNotes('');
                }}
                className="px-4 py-2 text-indigo-700 font-medium rounded-lg hover:bg-indigo-50 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleApprove}
                disabled={approving}
                className="px-4 py-2 bg-green-600 text-white font-medium rounded-lg hover:bg-green-700 disabled:opacity-50 transition"
              >
                {approving ? 'Approving...' : 'Approve & Deploy'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reject Modal */}
      {showRejectModal && selectedCandidate && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 modal-backdrop">
          <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
            <h3 className="text-lg font-semibold text-slate-900 mb-4">Reject Pattern</h3>

            <div className="mb-4">
              <label className="block text-sm font-medium text-slate-700 mb-1">Rejection Reason</label>
              <textarea
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Why is this pattern not suitable for L1 promotion?"
                rows={3}
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </div>

            <div className="flex justify-end space-x-3">
              <button
                onClick={() => {
                  setShowRejectModal(false);
                  setSelectedCandidate(null);
                  setRejectReason('');
                }}
                className="px-4 py-2 text-indigo-700 font-medium rounded-lg hover:bg-indigo-50 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleReject}
                disabled={approving || !rejectReason}
                className="px-4 py-2 bg-red-600 text-white font-medium rounded-lg hover:bg-red-700 disabled:opacity-50 transition"
              >
                {approving ? 'Rejecting...' : 'Reject'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Reject Modal */}
      {showBulkRejectModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 modal-backdrop">
          <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
            <h3 className="text-lg font-semibold text-slate-900 mb-2">
              Bulk Reject {selectedIds.size} Patterns
            </h3>
            <p className="text-sm text-slate-500 mb-4">
              This will reject all selected candidates with the same reason.
            </p>

            <div className="mb-4">
              <label className="block text-sm font-medium text-slate-700 mb-1">Rejection Reason</label>
              <textarea
                value={bulkRejectReason}
                onChange={(e) => setBulkRejectReason(e.target.value)}
                placeholder="Why are these patterns not suitable for L1 promotion?"
                rows={3}
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </div>

            <div className="flex justify-end space-x-3">
              <button
                onClick={() => {
                  setShowBulkRejectModal(false);
                  setBulkRejectReason('');
                }}
                className="px-4 py-2 text-indigo-700 font-medium rounded-lg hover:bg-indigo-50 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleBulkReject}
                disabled={bulkRejecting || !bulkRejectReason.trim()}
                className="px-4 py-2 bg-red-600 text-white font-medium rounded-lg hover:bg-red-700 disabled:opacity-50 transition"
              >
                {bulkRejecting ? 'Rejecting...' : `Reject ${selectedIds.size} Patterns`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

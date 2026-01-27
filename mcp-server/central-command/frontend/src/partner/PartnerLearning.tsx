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
    if (!selectedCandidate || !rejectReason) return;
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
        return 'bg-gray-100 text-gray-800';
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
        <span className="ml-3 text-gray-500">Loading learning data...</span>
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
          <div className="bg-white rounded-xl shadow-sm p-6">
            <p className="text-sm text-gray-500">Pending Candidates</p>
            <p className="text-2xl font-bold text-yellow-600">{stats.pending_candidates}</p>
          </div>
          <div className="bg-white rounded-xl shadow-sm p-6">
            <p className="text-sm text-gray-500">Active L1 Rules</p>
            <p className="text-2xl font-bold text-green-600">{stats.active_promoted_rules}</p>
          </div>
          <div className="bg-white rounded-xl shadow-sm p-6">
            <p className="text-sm text-gray-500">L1 Resolution Rate</p>
            <p className="text-2xl font-bold text-indigo-600">{formatPercentage(stats.l1_resolution_rate)}</p>
          </div>
          <div className="bg-white rounded-xl shadow-sm p-6">
            <p className="text-sm text-gray-500">Avg Success Rate</p>
            <p className="text-2xl font-bold text-blue-600">{formatPercentage(stats.avg_success_rate)}</p>
          </div>
        </div>
      )}

      {/* Promotion Candidates */}
      <div className="bg-white rounded-xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200">
          <h3 className="text-lg font-semibold text-gray-900">Promotion Candidates</h3>
          <p className="text-sm text-gray-500">Patterns eligible for L1 promotion (5+ occurrences, 90%+ success rate)</p>
        </div>

        {candidates.length === 0 ? (
          <div className="p-12 text-center">
            <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-gray-900 mb-2">No Promotion Candidates</h3>
            <p className="text-gray-500">Patterns will appear here once they meet the promotion criteria.</p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Pattern</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Site</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Occurrences</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Success Rate</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {candidates.map((candidate) => (
                <tr key={candidate.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4">
                    <div>
                      <p className="text-sm font-medium text-gray-900">
                        {candidate.recommended_action || 'Unknown Pattern'}
                      </p>
                      <p className="text-xs text-gray-500 font-mono">
                        {candidate.pattern_signature.substring(0, 12)}...
                      </p>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <p className="text-sm text-gray-900">{candidate.site_name}</p>
                  </td>
                  <td className="px-6 py-4">
                    <p className="text-sm text-gray-900">{candidate.total_occurrences}</p>
                    <p className="text-xs text-gray-500">
                      L2: {candidate.l2_resolutions}
                    </p>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`px-2 py-1 text-xs font-medium rounded-full ${
                      candidate.success_rate >= 0.95 ? 'bg-green-100 text-green-800' :
                      candidate.success_rate >= 0.90 ? 'bg-yellow-100 text-yellow-800' :
                      'bg-red-100 text-red-800'
                    }`}>
                      {formatPercentage(candidate.success_rate)}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`px-2 py-1 text-xs font-medium rounded-full ${getStatusColor(candidate.approval_status)}`}>
                      {candidate.approval_status === 'not_submitted' ? 'Pending Review' : candidate.approval_status}
                    </span>
                  </td>
                  <td className="px-6 py-4">
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
      <div className="bg-white rounded-xl shadow-sm overflow-hidden">
        <div
          className="px-6 py-4 border-b border-gray-200 flex items-center justify-between cursor-pointer"
          onClick={() => setShowPromotedRules(!showPromotedRules)}
        >
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Promoted Rules ({promotedRules.length})</h3>
            <p className="text-sm text-gray-500">Active L1 rules deployed from pattern promotions</p>
          </div>
          <svg
            className={`w-5 h-5 text-gray-500 transform transition ${showPromotedRules ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>

        {showPromotedRules && (
          promotedRules.length === 0 ? (
            <div className="p-8 text-center text-gray-500">
              No promoted rules yet. Approve promotion candidates to create L1 rules.
            </div>
          ) : (
            <table className="w-full">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Rule ID</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Site</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Deployments</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Promoted</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {promotedRules.map((rule) => (
                  <tr key={rule.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4">
                      <p className="text-sm font-medium text-gray-900 font-mono">{rule.rule_id}</p>
                    </td>
                    <td className="px-6 py-4">
                      <p className="text-sm text-gray-900">{rule.site_name || rule.site_id}</p>
                    </td>
                    <td className="px-6 py-4">
                      <p className="text-sm text-gray-900">{rule.deployment_count}</p>
                    </td>
                    <td className="px-6 py-4">
                      <p className="text-sm text-gray-900">{formatDate(rule.promoted_at)}</p>
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
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-6 w-full max-w-lg shadow-xl">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Approve Pattern for L1 Promotion</h3>

            <div className="space-y-4 mb-6">
              <div className="bg-gray-50 rounded-lg p-4">
                <p className="text-sm text-gray-500">Pattern</p>
                <p className="text-sm font-medium text-gray-900">{selectedCandidate.recommended_action}</p>
                <p className="text-xs text-gray-500 font-mono mt-1">{selectedCandidate.pattern_signature}</p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="bg-gray-50 rounded-lg p-4">
                  <p className="text-sm text-gray-500">Occurrences</p>
                  <p className="text-lg font-semibold text-gray-900">{selectedCandidate.total_occurrences}</p>
                </div>
                <div className="bg-gray-50 rounded-lg p-4">
                  <p className="text-sm text-gray-500">Success Rate</p>
                  <p className="text-lg font-semibold text-green-600">{formatPercentage(selectedCandidate.success_rate)}</p>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Custom Rule Name (optional)</label>
                <input
                  type="text"
                  value={customName}
                  onChange={(e) => setCustomName(e.target.value)}
                  placeholder="e.g., Firewall Auto-Heal"
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Notes (optional)</label>
                <textarea
                  value={approvalNotes}
                  onChange={(e) => setApprovalNotes(e.target.value)}
                  placeholder="Approval notes..."
                  rows={2}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
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
                className="px-4 py-2 text-gray-700 font-medium rounded-lg hover:bg-gray-100 transition"
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
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Reject Pattern</h3>

            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">Rejection Reason</label>
              <textarea
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Why is this pattern not suitable for L1 promotion?"
                rows={3}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </div>

            <div className="flex justify-end space-x-3">
              <button
                onClick={() => {
                  setShowRejectModal(false);
                  setSelectedCandidate(null);
                  setRejectReason('');
                }}
                className="px-4 py-2 text-gray-700 font-medium rounded-lg hover:bg-gray-100 transition"
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
    </div>
  );
};

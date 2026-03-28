import React, { useState, useEffect } from 'react';
import { Modal } from '../shared';
import { useSuggestRunbook, useCVERunbooks, useRemediateCVE } from '../../hooks';
import type { CVEDetail, RunbookSummary } from '../../types';

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-health-critical text-white',
  high: 'bg-orange-500 text-white',
  medium: 'bg-health-warning text-white',
  low: 'bg-blue-500 text-white',
  unknown: 'bg-fill-tertiary text-label-secondary',
};

interface RemediateModalProps {
  cveDetail: CVEDetail;
  onClose: () => void;
  onSuccess?: () => void;
}

export const RemediateModal: React.FC<RemediateModalProps> = ({ cveDetail, onClose, onSuccess }) => {
  const affectedSites = cveDetail.affected_appliances
    .filter((a) => a.status === 'open')
    .map((a) => a.site_id);
  const uniqueSites = [...new Set(affectedSites)];

  const [selectedSites, setSelectedSites] = useState<Set<string>>(new Set(uniqueSites));
  const [runbookId, setRunbookId] = useState('');
  const [expiresHours, setExpiresHours] = useState(24);
  const [reason, setReason] = useState('');
  const [resultMessage, setResultMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const { data: suggestion, isLoading: suggestLoading } = useSuggestRunbook(cveDetail.cve_id);
  const { data: runbooks = [], isLoading: runbooksLoading } = useCVERunbooks();
  const remediateMutation = useRemediateCVE();

  // Set suggested runbook when it loads
  useEffect(() => {
    if (suggestion?.suggested_runbook_id && !runbookId) {
      setRunbookId(suggestion.suggested_runbook_id);
    }
  }, [suggestion, runbookId]);

  const toggleSite = (siteId: string) => {
    setSelectedSites((prev) => {
      const next = new Set(prev);
      if (next.has(siteId)) {
        next.delete(siteId);
      } else {
        next.add(siteId);
      }
      return next;
    });
  };

  const toggleAll = () => {
    if (selectedSites.size === uniqueSites.length) {
      setSelectedSites(new Set());
    } else {
      setSelectedSites(new Set(uniqueSites));
    }
  };

  const handleSubmit = () => {
    if (!runbookId || selectedSites.size === 0) return;
    setResultMessage(null);
    remediateMutation.mutate(
      {
        cveId: cveDetail.cve_id,
        siteIds: [...selectedSites],
        runbookId,
        expiresHours,
        reason: reason || undefined,
      },
      {
        onSuccess: (data) => {
          setResultMessage({
            type: 'success',
            text: `Remediation orders created: ${data.orders_created}, skipped: ${data.orders_skipped}`,
          });
          onSuccess?.();
        },
        onError: (err) => {
          setResultMessage({
            type: 'error',
            text: err instanceof Error ? err.message : 'Remediation failed',
          });
        },
      },
    );
  };

  // Group runbooks by category for the dropdown
  const groupedRunbooks = runbooks.reduce<Record<string, RunbookSummary[]>>((acc, rb) => {
    const cat = rb.category || 'Uncategorized';
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(rb);
    return acc;
  }, {});

  return (
    <Modal isOpen onClose={onClose} title={`Remediate ${cveDetail.cve_id}`} size="lg">
      <div className="space-y-5">
        {/* Severity + CVSS header */}
        <div className="flex items-center gap-3">
          <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${SEVERITY_COLORS[cveDetail.severity] || SEVERITY_COLORS.unknown}`}>
            {cveDetail.severity.toUpperCase()}
          </span>
          {cveDetail.cvss_score && (
            <span className="text-sm text-label-tertiary">CVSS {cveDetail.cvss_score}</span>
          )}
          <span className="text-sm text-label-secondary truncate">{cveDetail.description?.slice(0, 120)}</span>
        </div>

        {/* Runbook selector */}
        <div>
          <label className="block text-xs font-medium text-label-tertiary uppercase mb-1.5">Runbook</label>
          {suggestLoading || runbooksLoading ? (
            <div className="flex items-center gap-2 text-sm text-label-tertiary">
              <div className="w-4 h-4 border-2 border-accent-primary border-t-transparent rounded-full animate-spin" />
              Loading runbooks...
            </div>
          ) : (
            <select
              value={runbookId}
              onChange={(e) => setRunbookId(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-fill-tertiary border border-separator rounded-ios-sm text-label-primary focus:outline-none focus:ring-1 focus:ring-accent-primary"
            >
              <option value="">Select a runbook...</option>
              {Object.entries(groupedRunbooks).map(([cat, rbs]) => (
                <optgroup key={cat} label={cat}>
                  {rbs.map((rb) => (
                    <option key={rb.runbook_id} value={rb.runbook_id}>
                      {rb.name} ({rb.runbook_id})
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          )}
          {suggestion?.suggested_runbook_id && runbookId === suggestion.suggested_runbook_id && (
            <p className="text-xs text-health-healthy mt-1">Auto-suggested based on CVE analysis</p>
          )}
        </div>

        {/* Affected sites */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs font-medium text-label-tertiary uppercase">
              Affected Sites ({selectedSites.size}/{uniqueSites.length})
            </label>
            <button
              onClick={toggleAll}
              className="text-xs text-accent-primary hover:underline"
            >
              {selectedSites.size === uniqueSites.length ? 'Deselect All' : 'Select All'}
            </button>
          </div>
          <div className="max-h-40 overflow-y-auto border border-separator rounded-ios-sm divide-y divide-separator">
            {uniqueSites.length === 0 ? (
              <p className="text-sm text-label-tertiary px-3 py-2">No open affected sites</p>
            ) : (
              uniqueSites.map((siteId) => (
                <label key={siteId} className="flex items-center gap-3 px-3 py-2 hover:bg-fill-tertiary/50 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedSites.has(siteId)}
                    onChange={() => toggleSite(siteId)}
                    className="rounded border-separator text-accent-primary focus:ring-accent-primary"
                  />
                  <span className="text-sm font-mono text-label-primary">{siteId}</span>
                </label>
              ))
            )}
          </div>
        </div>

        {/* Expiry + Reason row */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-label-tertiary uppercase mb-1.5">Expires (hours)</label>
            <select
              value={expiresHours}
              onChange={(e) => setExpiresHours(Number(e.target.value))}
              className="w-full px-3 py-2 text-sm bg-fill-tertiary border border-separator rounded-ios-sm text-label-primary focus:outline-none focus:ring-1 focus:ring-accent-primary"
            >
              <option value={6}>6 hours</option>
              <option value={12}>12 hours</option>
              <option value={24}>24 hours</option>
              <option value={48}>48 hours</option>
              <option value={72}>72 hours</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-label-tertiary uppercase mb-1.5">Reason (optional)</label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Emergency patch for critical CVE"
              className="w-full px-3 py-2 text-sm bg-fill-tertiary border border-separator rounded-ios-sm text-label-primary placeholder-label-tertiary focus:outline-none focus:ring-1 focus:ring-accent-primary"
            />
          </div>
        </div>

        {/* Result message */}
        {resultMessage && (
          <div className={`px-3 py-2 rounded-ios-sm text-sm ${resultMessage.type === 'success' ? 'bg-health-healthy/20 text-health-healthy' : 'bg-health-critical/20 text-health-critical'}`}>
            {resultMessage.text}
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center justify-end gap-3 pt-2 border-t border-separator">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-label-secondary bg-fill-secondary rounded-ios-sm hover:bg-fill-tertiary transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={remediateMutation.isPending || !runbookId || selectedSites.size === 0}
            className="px-4 py-2 text-sm font-medium text-white bg-accent-primary rounded-ios-sm hover:bg-accent-primary/90 disabled:opacity-50 transition-colors"
          >
            {remediateMutation.isPending
              ? 'Submitting...'
              : `Confirm Remediation (${selectedSites.size} site${selectedSites.size !== 1 ? 's' : ''})`}
          </button>
        </div>
      </div>
    </Modal>
  );
};

export default RemediateModal;

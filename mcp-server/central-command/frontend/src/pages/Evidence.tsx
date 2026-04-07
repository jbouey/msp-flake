import React, { useState, useCallback } from 'react';
import { GlassCard, Spinner, Modal } from '../components/shared';
import { useSites, useEvidenceBundles, useVerifyBundle, useVerifyBatch, useBlockchainStatus } from '../hooks';
import type { BundleVerifyResult, BatchVerifyResult } from '../utils/api';

// -- Inline SVG icons --

const ShieldCheckIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
  </svg>
);

const CheckIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
  </svg>
);

const XIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
  </svg>
);

const MinusIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M20 12H4" />
  </svg>
);

const LinkIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m9.86-2.504a4.5 4.5 0 00-1.242-7.244l-4.5-4.5a4.5 4.5 0 00-6.364 6.364l1.757 1.757" />
  </svg>
);

// -- Helper functions --

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return '--';
  const date = new Date(dateStr);
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function truncateId(id: string, len = 12): string {
  if (id.length <= len) return id;
  return `${id.slice(0, 8)}...${id.slice(-4)}`;
}

/** Render a pass/fail/null verification indicator */
const VerifyIndicator: React.FC<{ value: boolean | null; label: string }> = ({ value, label }) => {
  if (value === null || value === undefined) {
    return (
      <div className="flex items-center gap-2 text-label-tertiary">
        <MinusIcon className="w-4 h-4" />
        <span className="text-sm">{label}: N/A</span>
      </div>
    );
  }
  return (
    <div className={`flex items-center gap-2 ${value ? 'text-health-healthy' : 'text-health-critical'}`}>
      {value ? <CheckIcon className="w-4 h-4" /> : <XIcon className="w-4 h-4" />}
      <span className="text-sm">{label}: {value ? 'Pass' : 'Fail'}</span>
    </div>
  );
};

/** OTS status badge */
const OtsBadge: React.FC<{ status: string }> = ({ status }) => {
  const colors: Record<string, string> = {
    anchored: 'bg-green-100 text-green-800',
    verified: 'bg-green-100 text-green-800',
    pending: 'bg-yellow-100 text-yellow-800',
    none: 'bg-fill-secondary text-label-tertiary',
    expired: 'bg-red-100 text-red-800',
    failed: 'bg-red-100 text-red-800',
  };
  const cls = colors[status] || colors['none'];
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {status}
    </span>
  );
};

// -- Main page component --

export const Evidence: React.FC = () => {
  const [selectedSiteId, setSelectedSiteId] = useState<string | null>(null);
  const [verifyResult, setVerifyResult] = useState<BundleVerifyResult | null>(null);
  const [batchResult, setBatchResult] = useState<BatchVerifyResult | null>(null);
  const [showVerifyModal, setShowVerifyModal] = useState(false);

  // Data hooks
  const { data: sitesData } = useSites();
  const sites = sitesData?.sites || [];

  const { data: bundlesData, isLoading: bundlesLoading } = useEvidenceBundles(selectedSiteId, 50);
  const { data: blockchainData } = useBlockchainStatus(selectedSiteId);

  // Mutation hooks
  const verifyBundleMutation = useVerifyBundle();
  const verifyBatchMutation = useVerifyBatch();

  const handleVerifyBundle = useCallback((bundleId: string) => {
    setVerifyResult(null);
    setShowVerifyModal(true);
    verifyBundleMutation.mutate(bundleId, {
      onSuccess: (data) => setVerifyResult(data),
    });
  }, [verifyBundleMutation]);

  const handleBatchVerify = useCallback(() => {
    if (!selectedSiteId) return;
    setBatchResult(null);
    verifyBatchMutation.mutate(selectedSiteId, {
      onSuccess: (data) => setBatchResult(data),
    });
  }, [selectedSiteId, verifyBatchMutation]);

  const bundles = bundlesData?.bundles || [];
  const totalBundles = bundlesData?.total || 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary">Evidence Verification</h1>
          <p className="text-label-tertiary mt-1">
            Verify evidence bundle integrity, chain linkage, and blockchain anchoring
          </p>
        </div>

        <div className="flex items-center gap-3">
          {/* Site selector */}
          <select
            value={selectedSiteId || ''}
            onChange={(e) => {
              setSelectedSiteId(e.target.value || null);
              setBatchResult(null);
              setVerifyResult(null);
            }}
            className="rounded-ios-md border border-separator-light bg-fill-primary text-label-primary px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ios-blue"
          >
            <option value="">Select a site...</option>
            {sites.map((s) => (
              <option key={s.site_id} value={s.site_id}>
                {s.clinic_name || `${s.site_id.slice(0, 12)}...`}
              </option>
            ))}
          </select>

          {/* Batch verify button */}
          <button
            onClick={handleBatchVerify}
            disabled={!selectedSiteId || verifyBatchMutation.isPending}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-ios-md bg-ios-blue text-white text-sm font-medium hover:bg-ios-blue/90 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {verifyBatchMutation.isPending ? (
              <Spinner size="sm" />
            ) : (
              <ShieldCheckIcon className="w-4 h-4" />
            )}
            Verify All Recent (24h)
          </button>
        </div>
      </div>

      {/* No site selected state */}
      {!selectedSiteId && (
        <GlassCard>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <ShieldCheckIcon className="w-12 h-12 text-label-tertiary mb-3" />
            <h2 className="text-lg font-semibold text-label-primary mb-1">Select a Site</h2>
            <p className="text-label-tertiary text-sm max-w-md">
              Choose a site from the dropdown above to view evidence bundles, verify chain integrity,
              and check blockchain anchoring status.
            </p>
          </div>
        </GlassCard>
      )}

      {/* Blockchain status summary */}
      {selectedSiteId && blockchainData && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <GlassCard padding="md">
            <div className="text-xs text-label-tertiary uppercase tracking-wide">Total Bundles</div>
            <div className="text-2xl font-semibold text-label-primary mt-1">
              {blockchainData.evidence_chain.total_bundles}
            </div>
          </GlassCard>
          <GlassCard padding="md">
            <div className="text-xs text-label-tertiary uppercase tracking-wide">Signed</div>
            <div className="text-2xl font-semibold text-label-primary mt-1">
              {blockchainData.evidence_chain.signed_bundles}
              <span className="text-sm text-label-tertiary font-normal ml-1">
                / {blockchainData.evidence_chain.total_bundles}
              </span>
            </div>
          </GlassCard>
          <GlassCard padding="md">
            <div className="text-xs text-label-tertiary uppercase tracking-wide">BTC Anchored</div>
            <div className="text-2xl font-semibold text-label-primary mt-1">
              {blockchainData.blockchain.anchored + blockchainData.blockchain.verified}
              <span className="text-sm text-label-tertiary font-normal ml-1">
                ({blockchainData.blockchain.anchor_rate_pct}%)
              </span>
            </div>
          </GlassCard>
          <GlassCard padding="md">
            <div className="text-xs text-label-tertiary uppercase tracking-wide">Chain Length</div>
            <div className="text-2xl font-semibold text-label-primary mt-1">
              {blockchainData.evidence_chain.chain_length}
            </div>
          </GlassCard>
        </div>
      )}

      {/* Batch verify result */}
      {batchResult && (
        <GlassCard>
          <div className="flex items-start gap-4">
            <div className={`flex-shrink-0 p-2 rounded-ios-md ${batchResult.failed === 0 ? 'bg-green-100' : 'bg-red-100'}`}>
              {batchResult.failed === 0 ? (
                <CheckIcon className="w-6 h-6 text-green-700" />
              ) : (
                <XIcon className="w-6 h-6 text-red-700" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-base font-semibold text-label-primary">
                Batch Verification: {batchResult.failed === 0 ? 'All Passed' : `${batchResult.failed} Failure${batchResult.failed !== 1 ? 's' : ''}`}
              </h3>
              <p className="text-sm text-label-tertiary mt-1">
                Checked {batchResult.total} bundle{batchResult.total !== 1 ? 's' : ''} from the last 24 hours.{' '}
                {batchResult.passed} passed, {batchResult.failed} failed.
              </p>
              {batchResult.failures.length > 0 && (
                <div className="mt-3 space-y-2">
                  {batchResult.failures.map((f) => (
                    <div key={f.bundle_id} className="flex items-center gap-2 text-sm text-health-critical">
                      <XIcon className="w-3.5 h-3.5 flex-shrink-0" />
                      <span className="font-mono text-xs">{truncateId(f.bundle_id)}</span>
                      <span>at position {f.chain_position}:</span>
                      <span className="text-label-secondary">{f.reasons.join(', ')}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </GlassCard>
      )}

      {/* Bundle table */}
      {selectedSiteId && (
        <GlassCard padding="none">
          <div className="px-6 py-4 border-b border-separator-light flex items-center justify-between">
            <h2 className="text-base font-semibold text-label-primary">
              Evidence Bundles
              {totalBundles > 0 && (
                <span className="text-sm font-normal text-label-tertiary ml-2">({totalBundles} total)</span>
              )}
            </h2>
          </div>

          {bundlesLoading ? (
            <div className="flex items-center justify-center py-12">
              <Spinner size="md" />
            </div>
          ) : bundles.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <p className="text-label-tertiary text-sm">No evidence bundles found for this site.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-separator-light text-label-tertiary text-left">
                    <th className="px-6 py-3 font-medium">Bundle ID</th>
                    <th className="px-6 py-3 font-medium">Chain #</th>
                    <th className="px-6 py-3 font-medium">Type</th>
                    <th className="px-6 py-3 font-medium">Checked At</th>
                    <th className="px-6 py-3 font-medium text-center">Signed</th>
                    <th className="px-6 py-3 font-medium">OTS Status</th>
                    <th className="px-6 py-3 font-medium">BTC Block</th>
                    <th className="px-6 py-3 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-separator-light">
                  {bundles.map((b) => (
                    <tr key={b.bundle_id} className="hover:bg-fill-secondary/50 transition-colors">
                      <td className="px-6 py-3 font-mono text-xs text-label-primary">
                        {truncateId(b.bundle_id)}
                      </td>
                      <td className="px-6 py-3 text-label-primary">
                        {b.chain_position}
                      </td>
                      <td className="px-6 py-3 text-label-secondary">
                        {b.check_type || 'compliance'}
                      </td>
                      <td className="px-6 py-3 text-label-secondary">
                        {formatDateTime(b.checked_at)}
                      </td>
                      <td className="px-6 py-3 text-center">
                        {b.signed ? (
                          <CheckIcon className="w-4 h-4 text-health-healthy inline" />
                        ) : (
                          <MinusIcon className="w-4 h-4 text-label-tertiary inline" />
                        )}
                      </td>
                      <td className="px-6 py-3">
                        <OtsBadge status={b.ots_status} />
                      </td>
                      <td className="px-6 py-3 text-label-secondary font-mono text-xs">
                        {b.bitcoin_block || '--'}
                      </td>
                      <td className="px-6 py-3 text-right">
                        <button
                          onClick={() => handleVerifyBundle(b.bundle_id)}
                          className="inline-flex items-center gap-1 px-3 py-1 rounded-ios-sm text-xs font-medium text-ios-blue hover:bg-ios-blue/10 transition-colors"
                        >
                          <ShieldCheckIcon className="w-3.5 h-3.5" />
                          Verify
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </GlassCard>
      )}

      {/* Recent Bitcoin anchors */}
      {selectedSiteId && blockchainData && blockchainData.recent_anchors.length > 0 && (
        <GlassCard>
          <h2 className="text-base font-semibold text-label-primary mb-4">Recent Bitcoin Anchors</h2>
          <div className="space-y-3">
            {blockchainData.recent_anchors.map((anchor) => (
              <div key={anchor.bundle_id} className="flex items-center justify-between py-2 border-b border-separator-light last:border-0">
                <div className="flex items-center gap-3">
                  <div className="flex-shrink-0 w-8 h-8 rounded-ios-sm bg-ios-orange/10 flex items-center justify-center">
                    <LinkIcon className="w-4 h-4 text-ios-orange" />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-label-primary">
                      Block #{anchor.bitcoin_block}
                    </div>
                    <div className="text-xs text-label-tertiary">
                      {anchor.check_type} &middot; {formatDateTime(anchor.anchored_at)}
                    </div>
                  </div>
                </div>
                <a
                  href={anchor.blockstream_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-ios-blue hover:underline"
                >
                  View on Blockstream
                </a>
              </div>
            ))}
          </div>
        </GlassCard>
      )}

      {/* Single bundle verification modal */}
      <Modal
        isOpen={showVerifyModal}
        onClose={() => {
          setShowVerifyModal(false);
          setVerifyResult(null);
        }}
        title="Bundle Verification"
        size="lg"
      >
        {verifyBundleMutation.isPending ? (
          <div className="flex items-center justify-center py-8">
            <Spinner size="md" />
            <span className="ml-3 text-label-tertiary text-sm">Verifying bundle integrity...</span>
          </div>
        ) : verifyBundleMutation.isError ? (
          <div className="text-center py-8">
            <XIcon className="w-10 h-10 text-health-critical mx-auto mb-2" />
            <p className="text-label-primary font-medium">Verification Failed</p>
            <p className="text-sm text-label-tertiary mt-1">
              {verifyBundleMutation.error?.message || 'An error occurred during verification.'}
            </p>
          </div>
        ) : verifyResult ? (
          <div className="space-y-5">
            {/* Bundle info */}
            <div className="bg-fill-secondary rounded-ios-md p-4">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <span className="text-label-tertiary">Bundle ID:</span>
                  <div className="font-mono text-xs text-label-primary mt-0.5">{truncateId(verifyResult.bundle_id, 20)}</div>
                </div>
                <div>
                  <span className="text-label-tertiary">Chain Position:</span>
                  <div className="text-label-primary mt-0.5">#{verifyResult.chain_position}</div>
                </div>
                <div>
                  <span className="text-label-tertiary">Type:</span>
                  <div className="text-label-primary mt-0.5">{verifyResult.bundle_summary.bundle_type}</div>
                </div>
                <div>
                  <span className="text-label-tertiary">Checks:</span>
                  <div className="text-label-primary mt-0.5">{verifyResult.bundle_summary.check_count}</div>
                </div>
              </div>
            </div>

            {/* Verification checks */}
            <div>
              <h4 className="text-sm font-semibold text-label-primary mb-3">Integrity Checks</h4>
              <div className="space-y-2">
                <VerifyIndicator value={verifyResult.verification.hash_valid} label="Hash Integrity" />
                <VerifyIndicator value={verifyResult.verification.chain_valid} label="Chain Hash" />
                <VerifyIndicator value={verifyResult.verification.chain_prev_valid} label="Previous Link" />
                <VerifyIndicator value={verifyResult.verification.chain_next_valid} label="Forward Link" />
                <VerifyIndicator value={verifyResult.verification.signature_valid} label="Ed25519 Signature" />
              </div>
              {verifyResult.verification.signature_key_id && (
                <p className="text-xs text-label-tertiary mt-2">
                  Key: <span className="font-mono">{verifyResult.verification.signature_key_id}</span>
                </p>
              )}
            </div>

            {/* Blockchain status */}
            <div>
              <h4 className="text-sm font-semibold text-label-primary mb-3">Blockchain Anchor</h4>
              <div className="flex items-center gap-2 mb-2">
                <OtsBadge status={verifyResult.blockchain.status} />
                {verifyResult.blockchain.block_height && (
                  <span className="text-sm text-label-secondary">Block #{verifyResult.blockchain.block_height}</span>
                )}
              </div>
              {verifyResult.blockchain.explorer_url && (
                <a
                  href={verifyResult.blockchain.explorer_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-ios-blue hover:underline"
                >
                  View on blockchain explorer
                </a>
              )}
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
};

export default Evidence;

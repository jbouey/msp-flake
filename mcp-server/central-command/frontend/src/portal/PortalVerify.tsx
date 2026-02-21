import React, { useEffect, useState } from 'react';
import { useSearchParams, useParams, useNavigate } from 'react-router-dom';

interface VerificationResult {
  status: 'valid' | 'invalid' | 'empty' | 'error' | 'verified' | 'broken' | 'signature_invalid';
  chain_length: number | null;
  first_bundle: string | null;
  last_bundle: string | null;
  first_timestamp: string | null;
  last_timestamp: string | null;
  signatures_valid: number | null;
  signatures_total: number | null;
  error: string | null;
  bundle_id?: string | null;
}

interface BundleInfo {
  bundle_id: string;
  created_at: string;
  checked_at: string;
  checks_count: number;
  summary: Record<string, unknown>;
  prev_hash: string;
  bundle_hash: string;
  is_signed: boolean;
  signed_by: string | null;
  ots_status?: string;
  bitcoin_block?: number | null;
  anchored_at?: string | null;
}

interface BundlesResponse {
  site_id: string;
  bundles: BundleInfo[];
  total: number;
  limit: number;
  offset: number;
}

interface BlockchainAnchor {
  bitcoin_block: number;
  anchored_at: string | null;
  bundle_id: string;
  check_type: string;
  checked_at: string | null;
  blockstream_url: string;
}

interface BlockchainStatus {
  site_id: string;
  blockchain: {
    total_proofs: number;
    anchored: number;
    verified: number;
    pending: number;
    expired: number;
    anchor_rate_pct: number;
    first_bitcoin_block: number | null;
    latest_bitcoin_block: number | null;
    last_anchored: string | null;
    oldest_pending: string | null;
    blockstream_url: string | null;
  };
  evidence_chain: {
    total_bundles: number;
    signed_bundles: number;
    verified_signatures: number;
    chain_length: number;
    first_evidence: string | null;
    last_evidence: string | null;
  };
  recent_anchors: BlockchainAnchor[];
}

const StatusBadge: React.FC<{ status: VerificationResult['status'] }> = ({ status }) => {
  const colors: Record<string, string> = {
    valid: 'bg-green-100 text-green-800 border-green-200',
    verified: 'bg-green-100 text-green-800 border-green-200',
    invalid: 'bg-red-100 text-red-800 border-red-200',
    broken: 'bg-red-100 text-red-800 border-red-200',
    signature_invalid: 'bg-orange-100 text-orange-800 border-orange-200',
    empty: 'bg-slate-100 text-slate-800 border-slate-200',
    error: 'bg-red-100 text-red-800 border-red-200',
  };
  const labels: Record<string, string> = {
    valid: 'Chain Valid',
    verified: 'Chain Verified',
    invalid: 'Chain Invalid',
    broken: 'Chain Broken',
    signature_invalid: 'Signature Invalid',
    empty: 'No Evidence',
    error: 'Error',
  };
  const icons: Record<string, string> = {
    valid: '\u2713',
    verified: '\u2713',
    invalid: '\u2717',
    broken: '\u2717',
    signature_invalid: '\u26A0',
    empty: '\u25CB',
    error: '!',
  };

  return (
    <span className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium border ${colors[status] || colors.error}`}>
      <span className="text-lg">{icons[status] || '?'}</span>
      {labels[status] || status}
    </span>
  );
};

const OtsBadge: React.FC<{ status: string; block?: number | null }> = ({ status, block }) => {
  const config: Record<string, { bg: string; label: string }> = {
    anchored: { bg: 'bg-amber-100 text-amber-800', label: 'Bitcoin Anchored' },
    verified: { bg: 'bg-green-100 text-green-800', label: 'Bitcoin Verified' },
    pending: { bg: 'bg-blue-100 text-blue-700', label: 'Pending' },
    none: { bg: 'bg-slate-100 text-slate-500', label: 'No Proof' },
  };
  const c = config[status] || config.none;
  return (
    <span className={`text-xs px-2 py-1 rounded inline-flex items-center gap-1 ${c.bg}`}>
      {(status === 'anchored' || status === 'verified') && (
        <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 15l-5-5 1.41-1.41L11 14.17l7.59-7.59L20 8l-9 9z"/>
        </svg>
      )}
      {c.label}
      {block && (
        <a
          href={`https://blockstream.info/block-height/${block}`}
          target="_blank"
          rel="noopener noreferrer"
          className="underline ml-1"
          title="View on Bitcoin blockchain"
        >
          #{block.toLocaleString()}
        </a>
      )}
    </span>
  );
};

const HashDisplay: React.FC<{ label: string; hash: string; isGenesis?: boolean }> = ({ label, hash, isGenesis }) => (
  <div className="bg-slate-50 rounded-lg p-3">
    <span className="text-xs text-slate-500 block mb-1">{label}</span>
    <code className={`text-xs font-mono break-all ${isGenesis ? 'text-slate-400' : 'text-slate-700'}`}>
      {isGenesis ? '(genesis - all zeros)' : hash}
    </code>
  </div>
);

const BundleTimeline: React.FC<{ bundles: BundleInfo[] }> = ({ bundles }) => {
  if (bundles.length === 0) return null;

  return (
    <div className="space-y-4">
      {bundles.map((bundle, index) => {
        const isGenesis = bundle.prev_hash === '0'.repeat(64);
        return (
          <div key={bundle.bundle_id} className="relative pl-8">
            {/* Timeline connector */}
            {index < bundles.length - 1 && (
              <div className="absolute left-3 top-8 bottom-0 w-0.5 bg-slate-200" />
            )}
            {/* Timeline dot */}
            <div className={`absolute left-0 top-2 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold
              ${isGenesis ? 'bg-blue-100 text-blue-600' : 'bg-green-100 text-green-600'}`}>
              {isGenesis ? 'G' : bundles.length - index}
            </div>

            <div className="bg-white border border-slate-200 rounded-xl p-4 hover:shadow-sm transition">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h4 className="font-semibold text-slate-900">{bundle.bundle_id}</h4>
                  <p className="text-sm text-slate-500">
                    {new Date(bundle.created_at || bundle.checked_at).toLocaleString()}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {bundle.is_signed && (
                    <span className="text-xs bg-green-100 text-green-700 px-2 py-1 rounded flex items-center gap-1" title={`Signed by: ${bundle.signed_by}`}>
                      <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M2.166 4.999A11.954 11.954 0 0010 1.944 11.954 11.954 0 0017.834 5c.11.65.166 1.32.166 2.001 0 5.225-3.34 9.67-8 11.317C5.34 16.67 2 12.225 2 7c0-.682.057-1.35.166-2.001zm11.541 3.708a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                      </svg>
                      Signed
                    </span>
                  )}
                  <OtsBadge status={bundle.ots_status || 'none'} block={bundle.bitcoin_block} />
                  <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded">
                    {bundle.checks_count} checks
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <HashDisplay
                  label="Previous Hash"
                  hash={bundle.prev_hash}
                  isGenesis={isGenesis}
                />
                <HashDisplay
                  label="Bundle Hash"
                  hash={bundle.bundle_hash}
                />
              </div>

              {/* Chain link visualization */}
              {!isGenesis && (
                <div className="mt-3 flex items-center gap-2 text-xs text-slate-400">
                  <span className="font-mono">{bundle.prev_hash.substring(0, 8)}...</span>
                  <span>links to previous bundle</span>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

const BlockchainSection: React.FC<{ data: BlockchainStatus }> = ({ data }) => {
  const bc = data.blockchain;
  const anchored = bc.anchored + bc.verified;

  return (
    <section className="bg-white rounded-2xl border border-slate-200 shadow-sm p-8 mb-8">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-amber-100 rounded-full flex items-center justify-center">
            <svg className="w-5 h-5 text-amber-700" viewBox="0 0 24 24" fill="currentColor">
              <path d="M11.944 17.97L4.58 13.62 11.943 24l7.37-10.38-7.372 4.35h.003zM12.056 0L4.69 12.223l7.365 4.354 7.365-4.35L12.056 0z"/>
            </svg>
          </div>
          <div>
            <h2 className="text-xl font-semibold text-slate-900">Bitcoin Blockchain Anchoring</h2>
            <p className="text-sm text-slate-500">Evidence timestamps independently verified on the Bitcoin network</p>
          </div>
        </div>
        {anchored > 0 && (
          <span className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium border bg-amber-50 text-amber-800 border-amber-200">
            <span className="text-lg">{'\u2713'}</span>
            {bc.anchor_rate_pct}% Anchored
          </span>
        )}
      </div>

      {/* Auditor explanation */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-6">
        <p className="text-sm text-amber-900">
          <strong>For auditors and legal:</strong> Each evidence bundle's SHA-256 hash is submitted to the
          Bitcoin blockchain via OpenTimestamps. Once anchored in a Bitcoin block, the timestamp becomes
          independently verifiable by any third party — proving this evidence existed at a specific point in
          time and has not been altered. This provides non-repudiation that cannot be forged or backdated.
        </p>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-slate-50 rounded-lg p-4 text-center">
          <span className="text-3xl font-bold text-amber-600">{anchored.toLocaleString()}</span>
          <p className="text-sm text-slate-500 mt-1">Bitcoin Anchored</p>
        </div>
        <div className="bg-slate-50 rounded-lg p-4 text-center">
          <span className="text-3xl font-bold text-slate-900">{bc.total_proofs.toLocaleString()}</span>
          <p className="text-sm text-slate-500 mt-1">Total Proofs</p>
        </div>
        <div className="bg-slate-50 rounded-lg p-4 text-center">
          <span className="text-3xl font-bold text-green-600">{bc.anchor_rate_pct}%</span>
          <p className="text-sm text-slate-500 mt-1">Anchor Rate</p>
        </div>
        <div className="bg-slate-50 rounded-lg p-4 text-center">
          {bc.latest_bitcoin_block ? (
            <a
              href={`https://blockstream.info/block-height/${bc.latest_bitcoin_block}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-2xl font-bold text-blue-600 hover:underline"
            >
              #{bc.latest_bitcoin_block.toLocaleString()}
            </a>
          ) : (
            <span className="text-2xl font-bold text-slate-400">--</span>
          )}
          <p className="text-sm text-slate-500 mt-1">Latest Block</p>
        </div>
      </div>

      {/* Block range */}
      {bc.first_bitcoin_block && bc.latest_bitcoin_block && (
        <div className="bg-slate-50 rounded-lg p-4 mb-6">
          <div className="flex items-center justify-between text-sm">
            <div>
              <span className="text-slate-500">First anchor: </span>
              <a
                href={`https://blockstream.info/block-height/${bc.first_bitcoin_block}`}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-blue-600 hover:underline"
              >
                Block #{bc.first_bitcoin_block.toLocaleString()}
              </a>
            </div>
            <div className="text-slate-400">
              {(bc.latest_bitcoin_block - bc.first_bitcoin_block).toLocaleString()} blocks span
            </div>
            <div>
              <span className="text-slate-500">Latest anchor: </span>
              <a
                href={`https://blockstream.info/block-height/${bc.latest_bitcoin_block}`}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-blue-600 hover:underline"
              >
                Block #{bc.latest_bitcoin_block.toLocaleString()}
              </a>
            </div>
          </div>
          {/* Visual bar */}
          <div className="mt-3 h-2 bg-slate-200 rounded-full overflow-hidden">
            <div className="h-full bg-amber-400 rounded-full" style={{ width: `${bc.anchor_rate_pct}%` }} />
          </div>
        </div>
      )}

      {/* Recent anchors table */}
      {data.recent_anchors.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Recent Bitcoin Anchors</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="text-left py-2 text-slate-500 font-medium">Bitcoin Block</th>
                  <th className="text-left py-2 text-slate-500 font-medium">Anchored</th>
                  <th className="text-left py-2 text-slate-500 font-medium">Evidence Bundle</th>
                  <th className="text-left py-2 text-slate-500 font-medium">Check Type</th>
                  <th className="text-left py-2 text-slate-500 font-medium">Verify</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_anchors.map((anchor) => (
                  <tr key={anchor.bundle_id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="py-2">
                      <a
                        href={anchor.blockstream_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-mono text-blue-600 hover:underline"
                      >
                        #{anchor.bitcoin_block.toLocaleString()}
                      </a>
                    </td>
                    <td className="py-2 text-slate-600">
                      {anchor.anchored_at ? new Date(anchor.anchored_at).toLocaleString() : '--'}
                    </td>
                    <td className="py-2">
                      <code className="text-xs font-mono text-slate-700">{anchor.bundle_id.substring(0, 20)}...</code>
                    </td>
                    <td className="py-2 text-slate-600">{anchor.check_type}</td>
                    <td className="py-2">
                      <a
                        href={anchor.blockstream_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-blue-600 hover:underline"
                      >
                        Blockstream
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {bc.pending > 0 && (
        <div className="mt-4 text-xs text-slate-500">
          {bc.pending} proof{bc.pending !== 1 ? 's' : ''} awaiting Bitcoin confirmation (typically 1-6 hours)
        </div>
      )}
    </section>
  );
};

export const PortalVerify: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { siteId } = useParams<{ siteId: string }>();
  const token = searchParams.get('token');

  const [verification, setVerification] = useState<VerificationResult | null>(null);
  const [bundles, setBundles] = useState<BundleInfo[]>([]);
  const [blockchain, setBlockchain] = useState<BlockchainStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!siteId) {
      setError('Invalid site ID');
      setLoading(false);
      return;
    }

    const headers: Record<string, string> = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    // Fetch verification status, bundles, and blockchain data in parallel
    Promise.all([
      fetch(`/api/evidence/sites/${siteId}/verify`, {
        credentials: 'include',
        headers
      }).then(r => r.json()),
      fetch(`/api/evidence/sites/${siteId}/bundles?limit=20`, {
        credentials: 'include',
        headers
      }).then(r => r.json()),
      fetch(`/api/evidence/sites/${siteId}/blockchain-status`, {
        credentials: 'include',
        headers
      }).then(r => r.ok ? r.json() : null),
    ])
      .then(([verifyData, bundlesData, blockchainData]: [VerificationResult, BundlesResponse, BlockchainStatus | null]) => {
        setVerification(verifyData);
        setBundles(bundlesData.bundles || []);
        if (blockchainData) setBlockchain(blockchainData);
      })
      .catch((e) => {
        setError(e.message || 'Failed to load verification data');
      })
      .finally(() => setLoading(false));
  }, [siteId, token]);

  const handleBackToDashboard = () => {
    const url = token
      ? `/portal/site/${siteId}/dashboard?token=${token}`
      : `/portal/site/${siteId}/dashboard`;
    navigate(url);
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-slate-600">Verifying evidence chain...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-lg p-8 text-center">
          <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <span className="text-3xl">!</span>
          </div>
          <h1 className="text-xl font-semibold text-slate-900 mb-2">Verification Error</h1>
          <p className="text-slate-600 mb-6">{error}</p>
          <button
            onClick={handleBackToDashboard}
            className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition"
          >
            Back to Dashboard
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b shadow-sm">
        <div className="max-w-5xl mx-auto px-6 py-4 flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Evidence Chain Verification</h1>
            <p className="text-sm text-slate-500">
              Cryptographic proof of compliance evidence integrity
            </p>
          </div>
          <button
            onClick={handleBackToDashboard}
            className="px-4 py-2 text-sm text-slate-600 hover:text-blue-700 hover:bg-blue-50 rounded-lg transition"
          >
            Back to Dashboard
          </button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8">
        {/* Verification Status Card */}
        <section className="bg-white rounded-2xl border border-slate-200 shadow-sm p-8 mb-8">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-semibold text-slate-900">Chain Integrity Status</h2>
            {verification && <StatusBadge status={verification.status} />}
          </div>

          {(verification?.status === 'valid' || verification?.status === 'verified') && (
            <div className="bg-green-50 border border-green-200 rounded-xl p-4 mb-6">
              <p className="text-green-800">
                All {verification.chain_length} evidence bundles form a valid cryptographic chain.
                Each bundle's hash correctly references its predecessor, ensuring no tampering has occurred.
                {verification.signatures_total !== null && verification.signatures_total > 0 && (
                  <span className="block mt-2">
                    <strong>{verification.signatures_valid}</strong> of <strong>{verification.signatures_total}</strong> bundles have valid Ed25519 signatures.
                  </span>
                )}
              </p>
            </div>
          )}

          {(verification?.status === 'invalid' || verification?.status === 'broken') && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-6">
              <p className="text-red-800">
                Chain integrity verification failed at bundle: <code className="font-mono">{verification.bundle_id}</code>
                <br />
                <span className="text-sm">{verification.error}</span>
              </p>
            </div>
          )}

          {verification?.status === 'signature_invalid' && (
            <div className="bg-orange-50 border border-orange-200 rounded-xl p-4 mb-6">
              <p className="text-orange-800">
                Ed25519 signature verification failed at bundle: <code className="font-mono">{verification.bundle_id}</code>
                <br />
                <span className="text-sm">{verification.error}</span>
                <br />
                <span className="text-sm mt-2 block">
                  Chain valid: {verification.chain_length} bundles | Signatures: {verification.signatures_valid}/{verification.signatures_total} valid
                </span>
              </p>
            </div>
          )}

          {verification?.status === 'empty' && (
            <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 mb-6">
              <p className="text-slate-600">
                No compliance evidence bundles have been recorded yet for this site.
                Evidence will appear here after the first compliance scan.
              </p>
            </div>
          )}

          {/* Chain Statistics */}
          {verification && verification.status !== 'empty' && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <span className="text-3xl font-bold text-slate-900">{verification.chain_length}</span>
                <p className="text-sm text-slate-500 mt-1">Total Bundles</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <span className="text-3xl font-bold text-green-600">
                  {verification.signatures_valid ?? 0}
                </span>
                <p className="text-sm text-slate-500 mt-1">Signed Bundles</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <span className="text-sm font-mono text-slate-700">
                  {verification.first_bundle?.substring(0, 12)}...
                </span>
                <p className="text-sm text-slate-500 mt-1">Genesis Bundle</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <span className="text-sm text-slate-700">
                  {verification.first_timestamp && new Date(verification.first_timestamp).toLocaleDateString()}
                </span>
                <p className="text-sm text-slate-500 mt-1">First Evidence</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <span className="text-sm text-slate-700">
                  {verification.last_timestamp && new Date(verification.last_timestamp).toLocaleDateString()}
                </span>
                <p className="text-sm text-slate-500 mt-1">Latest Evidence</p>
              </div>
            </div>
          )}
        </section>

        {/* Blockchain Anchoring Section */}
        {blockchain && blockchain.blockchain.total_proofs > 0 && (
          <BlockchainSection data={blockchain} />
        )}

        {/* Bundle Timeline */}
        {bundles.length > 0 && (
          <section className="bg-white rounded-2xl border border-slate-200 shadow-sm p-8">
            <h2 className="text-xl font-semibold text-slate-900 mb-6">Evidence Bundle Timeline</h2>
            <p className="text-sm text-slate-500 mb-6">
              Each bundle contains a cryptographic hash linking it to the previous bundle,
              forming an immutable chain. The genesis bundle (G) starts the chain with an all-zeros previous hash.
              Bundles with a Bitcoin anchor have their timestamp independently verifiable on the blockchain.
            </p>
            <BundleTimeline bundles={bundles} />
          </section>
        )}

        {/* How It Works */}
        <section className="mt-8 bg-blue-50 rounded-2xl border border-blue-200 p-8">
          <h2 className="text-lg font-semibold text-blue-900 mb-4">How Evidence Verification Works</h2>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-6 text-sm text-blue-800">
            <div>
              <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center text-blue-600 font-bold mb-3">1</div>
              <h3 className="font-semibold mb-1">Bundle Creation</h3>
              <p className="text-blue-700">
                Each compliance scan generates an evidence bundle containing check results, timestamps, and metadata.
              </p>
            </div>
            <div>
              <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center text-blue-600 font-bold mb-3">2</div>
              <h3 className="font-semibold mb-1">Hash Linking</h3>
              <p className="text-blue-700">
                The bundle is hashed using SHA-256, and includes the previous bundle's hash, creating a chain.
              </p>
            </div>
            <div>
              <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center text-blue-600 font-bold mb-3">3</div>
              <h3 className="font-semibold mb-1">Ed25519 Signing</h3>
              <p className="text-blue-700">
                Each bundle is signed with the appliance's Ed25519 private key, proving authenticity and origin.
              </p>
            </div>
            <div>
              <div className="w-10 h-10 bg-amber-100 rounded-full flex items-center justify-center text-amber-600 font-bold mb-3">4</div>
              <h3 className="font-semibold mb-1">Bitcoin Anchoring</h3>
              <p className="text-amber-800">
                The bundle hash is submitted to the Bitcoin blockchain via OpenTimestamps, creating a tamper-proof timestamp.
              </p>
            </div>
            <div>
              <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center text-blue-600 font-bold mb-3">5</div>
              <h3 className="font-semibold mb-1">Tamper Detection</h3>
              <p className="text-blue-700">
                Any modification breaks the chain hash, invalidates the signature, or mismatches the blockchain anchor.
              </p>
            </div>
          </div>
        </section>

        {/* Footer */}
        <footer className="mt-12 pt-8 border-t border-slate-200 text-center">
          <p className="text-sm text-slate-500">
            This verification page provides cryptographic proof of evidence integrity for HIPAA auditors.
            Bitcoin blockchain anchors can be independently verified at{' '}
            <a href="https://blockstream.info" target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
              blockstream.info
            </a>.
          </p>
          <div className="mt-4 flex items-center justify-center gap-2">
            <span className="text-xs text-slate-300">Powered by</span>
            <span className="text-sm font-semibold text-slate-500">OsirisCare</span>
          </div>
        </footer>
      </main>
    </div>
  );
};

export default PortalVerify;

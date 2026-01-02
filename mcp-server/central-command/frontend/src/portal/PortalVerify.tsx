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
}

interface BundlesResponse {
  site_id: string;
  bundles: BundleInfo[];
  total: number;
  limit: number;
  offset: number;
}

const StatusBadge: React.FC<{ status: VerificationResult['status'] }> = ({ status }) => {
  const colors: Record<string, string> = {
    valid: 'bg-green-100 text-green-800 border-green-200',
    verified: 'bg-green-100 text-green-800 border-green-200',
    invalid: 'bg-red-100 text-red-800 border-red-200',
    broken: 'bg-red-100 text-red-800 border-red-200',
    signature_invalid: 'bg-orange-100 text-orange-800 border-orange-200',
    empty: 'bg-gray-100 text-gray-800 border-gray-200',
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
    valid: '✓',
    verified: '✓',
    invalid: '✗',
    broken: '✗',
    signature_invalid: '⚠',
    empty: '○',
    error: '!',
  };

  return (
    <span className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium border ${colors[status] || colors.error}`}>
      <span className="text-lg">{icons[status] || '?'}</span>
      {labels[status] || status}
    </span>
  );
};

const HashDisplay: React.FC<{ label: string; hash: string; isGenesis?: boolean }> = ({ label, hash, isGenesis }) => (
  <div className="bg-gray-50 rounded-lg p-3">
    <span className="text-xs text-gray-500 block mb-1">{label}</span>
    <code className={`text-xs font-mono break-all ${isGenesis ? 'text-gray-400' : 'text-gray-700'}`}>
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
              <div className="absolute left-3 top-8 bottom-0 w-0.5 bg-gray-200" />
            )}
            {/* Timeline dot */}
            <div className={`absolute left-0 top-2 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold
              ${isGenesis ? 'bg-blue-100 text-blue-600' : 'bg-green-100 text-green-600'}`}>
              {isGenesis ? 'G' : bundles.length - index}
            </div>

            <div className="bg-white border border-gray-200 rounded-xl p-4 hover:shadow-sm transition">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h4 className="font-semibold text-gray-900">{bundle.bundle_id}</h4>
                  <p className="text-sm text-gray-500">
                    {new Date(bundle.created_at).toLocaleString()}
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
                  <span className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded">
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
                <div className="mt-3 flex items-center gap-2 text-xs text-gray-400">
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

export const PortalVerify: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { siteId } = useParams<{ siteId: string }>();
  const token = searchParams.get('token');

  const [verification, setVerification] = useState<VerificationResult | null>(null);
  const [bundles, setBundles] = useState<BundleInfo[]>([]);
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

    // Fetch verification status and bundles in parallel
    Promise.all([
      fetch(`/api/evidence/sites/${siteId}/verify`, {
        credentials: 'include',
        headers
      }).then(r => r.json()),
      fetch(`/api/evidence/sites/${siteId}/bundles?limit=20`, {
        credentials: 'include',
        headers
      }).then(r => r.json()),
    ])
      .then(([verifyData, bundlesData]: [VerificationResult, BundlesResponse]) => {
        setVerification(verifyData);
        setBundles(bundlesData.bundles || []);
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
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-gray-600">Verifying evidence chain...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-lg p-8 text-center">
          <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <span className="text-3xl">!</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 mb-2">Verification Error</h1>
          <p className="text-gray-600 mb-6">{error}</p>
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
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b shadow-sm">
        <div className="max-w-5xl mx-auto px-6 py-4 flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Evidence Chain Verification</h1>
            <p className="text-sm text-gray-500">
              Cryptographic proof of compliance evidence integrity
            </p>
          </div>
          <button
            onClick={handleBackToDashboard}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition"
          >
            Back to Dashboard
          </button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8">
        {/* Verification Status Card */}
        <section className="bg-white rounded-2xl border border-gray-200 shadow-sm p-8 mb-8">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-semibold text-gray-900">Chain Integrity Status</h2>
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
            <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 mb-6">
              <p className="text-gray-600">
                No compliance evidence bundles have been recorded yet for this site.
                Evidence will appear here after the first compliance scan.
              </p>
            </div>
          )}

          {/* Chain Statistics */}
          {verification && verification.status !== 'empty' && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <div className="bg-gray-50 rounded-lg p-4 text-center">
                <span className="text-3xl font-bold text-gray-900">{verification.chain_length}</span>
                <p className="text-sm text-gray-500 mt-1">Total Bundles</p>
              </div>
              <div className="bg-gray-50 rounded-lg p-4 text-center">
                <span className="text-3xl font-bold text-green-600">
                  {verification.signatures_valid ?? 0}
                </span>
                <p className="text-sm text-gray-500 mt-1">Signed Bundles</p>
              </div>
              <div className="bg-gray-50 rounded-lg p-4 text-center">
                <span className="text-sm font-mono text-gray-700">
                  {verification.first_bundle?.substring(0, 12)}...
                </span>
                <p className="text-sm text-gray-500 mt-1">Genesis Bundle</p>
              </div>
              <div className="bg-gray-50 rounded-lg p-4 text-center">
                <span className="text-sm text-gray-700">
                  {verification.first_timestamp && new Date(verification.first_timestamp).toLocaleDateString()}
                </span>
                <p className="text-sm text-gray-500 mt-1">First Evidence</p>
              </div>
              <div className="bg-gray-50 rounded-lg p-4 text-center">
                <span className="text-sm text-gray-700">
                  {verification.last_timestamp && new Date(verification.last_timestamp).toLocaleDateString()}
                </span>
                <p className="text-sm text-gray-500 mt-1">Latest Evidence</p>
              </div>
            </div>
          )}
        </section>

        {/* Bundle Timeline */}
        {bundles.length > 0 && (
          <section className="bg-white rounded-2xl border border-gray-200 shadow-sm p-8">
            <h2 className="text-xl font-semibold text-gray-900 mb-6">Evidence Bundle Timeline</h2>
            <p className="text-sm text-gray-500 mb-6">
              Each bundle contains a cryptographic hash linking it to the previous bundle,
              forming an immutable chain. The genesis bundle (G) starts the chain with an all-zeros previous hash.
            </p>
            <BundleTimeline bundles={bundles} />
          </section>
        )}

        {/* How It Works */}
        <section className="mt-8 bg-blue-50 rounded-2xl border border-blue-200 p-8">
          <h2 className="text-lg font-semibold text-blue-900 mb-4">How Evidence Verification Works</h2>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6 text-sm text-blue-800">
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
                Each bundle hash is signed with the server's Ed25519 private key, proving authenticity and origin.
              </p>
            </div>
            <div>
              <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center text-blue-600 font-bold mb-3">4</div>
              <h3 className="font-semibold mb-1">Tamper Detection</h3>
              <p className="text-blue-700">
                Any modification breaks the chain hash or invalidates the signature, revealing tampering.
              </p>
            </div>
          </div>
        </section>

        {/* Footer */}
        <footer className="mt-12 pt-8 border-t border-gray-200 text-center">
          <p className="text-sm text-gray-500">
            This verification page provides cryptographic proof of evidence integrity for HIPAA auditors.
          </p>
          <div className="mt-4 flex items-center justify-center gap-2">
            <span className="text-xs text-gray-300">Powered by</span>
            <span className="text-sm font-semibold text-gray-500">OsirisCare</span>
          </div>
        </footer>
      </main>
    </div>
  );
};

export default PortalVerify;

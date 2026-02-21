import React, { useEffect, useState } from 'react';
import { useSearchParams, useParams, useNavigate } from 'react-router-dom';
import { IncidentList } from './components';

// ─── Interfaces ──────────────────────────────────────────────────────────────

interface PortalSite {
  site_id: string;
  name: string;
  status: string;
  last_checkin?: string;
}

interface PortalKPIs {
  compliance_pct: number;
  patch_mttr_hours: number;
  mfa_coverage_pct: number;
  backup_success_rate: number;
  auto_fixes_24h: number;
  controls_passing: number;
  controls_warning: number;
  controls_failing: number;
}

interface PortalControl {
  rule_id: string;
  name: string;
  status: 'pass' | 'warn' | 'fail';
  severity: string;
  hipaa_controls: string[];
  checked_at?: string;
  scope_summary: string;
  auto_fix_triggered: boolean;
  fix_duration_sec?: number;
  exception_applied: boolean;
  exception_reason?: string;
  plain_english?: string;
  why_it_matters?: string;
  consequence?: string;
  what_we_check?: string;
  hipaa_section?: string;
}

interface PortalIncident {
  incident_id: string;
  incident_type: string;
  severity: string;
  auto_fixed: boolean;
  resolution_time_sec?: number;
  created_at: string;
  resolved_at?: string;
}

interface PortalData {
  site: PortalSite;
  kpis: PortalKPIs;
  controls: PortalControl[];
  incidents: PortalIncident[];
  generated_at: string;
}

interface BlockchainStatus {
  blockchain: {
    total_proofs: number;
    anchored: number;
    verified: number;
    pending: number;
    anchor_rate_pct: number;
    first_bitcoin_block: number | null;
    latest_bitcoin_block: number | null;
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
}

interface ChainVerification {
  chain_length: number;
  verified: number;
  broken_count: number;
  status: string;
  signatures_valid: number;
  signatures_total: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

type Grade = { letter: string; color: string; bg: string; border: string; message: string };

const getGrade = (pct: number): Grade => {
  if (pct >= 95) return { letter: 'A', color: 'text-green-600', bg: 'bg-green-50', border: 'border-green-200', message: 'Excellent — your compliance posture is strong.' };
  if (pct >= 85) return { letter: 'B', color: 'text-green-500', bg: 'bg-green-50', border: 'border-green-200', message: 'Good — your systems are well-maintained with minor areas to improve.' };
  if (pct >= 75) return { letter: 'C', color: 'text-orange-500', bg: 'bg-orange-50', border: 'border-orange-200', message: 'Fair — some compliance controls need attention.' };
  if (pct >= 60) return { letter: 'D', color: 'text-orange-600', bg: 'bg-orange-50', border: 'border-orange-200', message: 'Needs improvement — several compliance gaps require action.' };
  return { letter: 'F', color: 'text-red-600', bg: 'bg-red-50', border: 'border-red-200', message: 'Critical — immediate action needed to restore compliance.' };
};

const worstStatus = (statuses: Array<'pass' | 'warn' | 'fail'>): 'pass' | 'warn' | 'fail' => {
  if (statuses.includes('fail')) return 'fail';
  if (statuses.includes('warn')) return 'warn';
  return 'pass';
};

const statusLabel: Record<string, { label: string; bg: string }> = {
  pass: { label: 'Protected', bg: 'bg-green-100 text-green-800' },
  warn: { label: 'Attention Needed', bg: 'bg-orange-100 text-orange-800' },
  fail: { label: 'Action Required', bg: 'bg-red-100 text-red-800' },
};

// ─── Section 1: Hero Score ───────────────────────────────────────────────────

const HeroScore: React.FC<{ site: PortalSite; kpis: PortalKPIs; generatedAt: string }> = ({ site, kpis, generatedAt }) => {
  const grade = getGrade(kpis.compliance_pct);

  return (
    <section className={`${grade.bg} border ${grade.border} rounded-2xl p-8 mb-8`}>
      <div className="flex items-center justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-2xl font-bold text-slate-900">{site.name}</h1>
            <span className={`text-xs px-2 py-1 rounded-full font-medium ${
              site.status === 'online' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
            }`}>
              {site.status === 'online' ? 'Online' : 'Offline'}
            </span>
          </div>
          <p className="text-slate-700 mb-4">
            Your HIPAA compliance is being actively monitored and maintained.
          </p>
          <p className="text-slate-600 text-sm">{grade.message}</p>
          <div className="mt-4 flex items-center gap-6 text-sm text-slate-500">
            <span>
              Last verified: {new Date(generatedAt).toLocaleString()}
            </span>
            <span>
              {kpis.controls_passing} passing, {kpis.controls_warning} warning, {kpis.controls_failing} failing
            </span>
          </div>
        </div>

        <div className="flex flex-col items-center ml-8">
          <div className={`w-32 h-32 rounded-full border-4 ${grade.border} ${grade.bg} flex items-center justify-center`}>
            <span className={`text-6xl font-bold ${grade.color}`}>{grade.letter}</span>
          </div>
          <span className={`text-2xl font-bold mt-2 ${grade.color}`}>{kpis.compliance_pct}%</span>
          <span className="text-xs text-slate-500">Compliance Score</span>
        </div>
      </div>
    </section>
  );
};

// ─── Section 2: Status Cards ─────────────────────────────────────────────────

const CARD_GROUPS = [
  {
    title: 'Your Data Protection',
    ids: ['endpoint_drift', 'storage_posture'],
    fallbackText: 'Your systems are configured to protect data at rest and in transit.',
    icon: (
      <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
      </svg>
    ),
  },
  {
    title: 'Your Backup Status',
    ids: ['backup_success'],
    fallbackText: 'Your data backups are monitored for completeness and recoverability.',
    icon: (
      <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 15a4.5 4.5 0 004.5 4.5H18a3.75 3.75 0 001.332-7.257 3 3 0 00-3.758-3.848 5.25 5.25 0 00-10.233 2.33A4.502 4.502 0 002.25 15z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 12.75V17.25m0 0l-2.25-2.25M12 17.25l2.25-2.25" />
      </svg>
    ),
  },
  {
    title: 'Your System Security',
    ids: ['patch_freshness', 'secrets_hygiene', 'git_protections'],
    fallbackText: 'Your systems are patched, secrets are secured, and code is protected.',
    icon: (
      <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
      </svg>
    ),
  },
  {
    title: 'Your Access Controls',
    ids: ['mfa_coverage', 'privileged_access'],
    fallbackText: 'User authentication and privileged access are monitored and enforced.',
    icon: (
      <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" />
      </svg>
    ),
  },
];

const StatusCards: React.FC<{ controls: PortalControl[] }> = ({ controls }) => {
  const controlMap = new Map(controls.map(c => [c.rule_id, c]));

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
      {CARD_GROUPS.map((group) => {
        const matched = group.ids.map(id => controlMap.get(id)).filter(Boolean) as PortalControl[];
        const status = matched.length > 0 ? worstStatus(matched.map(c => c.status)) : 'pass';
        const sl = statusLabel[status];

        const plainTexts = matched
          .map(c => c.plain_english)
          .filter(Boolean);
        const description = plainTexts.length > 0 ? plainTexts.join(' ') : group.fallbackText;

        const cardBg = status === 'pass' ? 'bg-green-50 border-green-200'
          : status === 'warn' ? 'bg-orange-50 border-orange-200'
          : 'bg-red-50 border-red-200';

        const iconColor = status === 'pass' ? 'text-green-600'
          : status === 'warn' ? 'text-orange-600'
          : 'text-red-600';

        return (
          <div key={group.title} className={`rounded-xl border p-6 ${cardBg}`}>
            <div className="flex items-start justify-between mb-3">
              <div className={iconColor}>{group.icon}</div>
              <span className={`text-xs px-2 py-1 rounded-full font-medium ${sl.bg}`}>{sl.label}</span>
            </div>
            <h3 className="font-semibold text-slate-900 mb-1">{group.title}</h3>
            <p className="text-sm text-slate-600 leading-relaxed">{description}</p>
          </div>
        );
      })}
    </div>
  );
};

// ─── Section 3: Blockchain Trust Stamp ───────────────────────────────────────

const BlockchainTrustStamp: React.FC<{ data: BlockchainStatus | null }> = ({ data }) => {
  if (!data || data.blockchain.total_proofs === 0) {
    return (
      <section className="bg-slate-50 border border-slate-200 rounded-xl p-6 mb-8 text-center text-sm text-slate-500">
        Blockchain anchoring will begin after your first evidence records are created.
      </section>
    );
  }

  const bc = data.blockchain;
  const anchored = bc.anchored + bc.verified;

  return (
    <section className="bg-amber-50 border border-amber-200 rounded-xl p-6 mb-8">
      <div className="flex items-center gap-4 mb-4">
        <div className="w-10 h-10 bg-amber-100 rounded-full flex items-center justify-center flex-shrink-0">
          <svg className="w-5 h-5 text-amber-700" viewBox="0 0 24 24" fill="currentColor">
            <path d="M11.944 17.97L4.58 13.62 11.943 24l7.37-10.38-7.372 4.35h.003zM12.056 0L4.69 12.223l7.365 4.354 7.365-4.35L12.056 0z"/>
          </svg>
        </div>
        <div className="flex-1">
          <h3 className="font-semibold text-amber-900">Your compliance evidence is verified on the Bitcoin blockchain</h3>
          <p className="text-sm text-amber-800 mt-1">
            This means your records cannot be tampered with or backdated. Anyone can independently verify this.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-8 gap-y-2 text-sm text-amber-900">
        <span><strong>{anchored.toLocaleString()}</strong> records anchored</span>
        {bc.first_bitcoin_block && bc.latest_bitcoin_block && (
          <span>
            Block{' '}
            <a href={`https://blockstream.info/block-height/${bc.first_bitcoin_block}`} target="_blank" rel="noopener noreferrer" className="font-mono underline">
              #{bc.first_bitcoin_block.toLocaleString()}
            </a>
            {' \u2013 '}
            <a href={`https://blockstream.info/block-height/${bc.latest_bitcoin_block}`} target="_blank" rel="noopener noreferrer" className="font-mono underline">
              #{bc.latest_bitcoin_block.toLocaleString()}
            </a>
          </span>
        )}
        <span><strong>{bc.anchor_rate_pct}%</strong> anchor rate</span>
        {bc.blockstream_url && (
          <a
            href={bc.blockstream_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-amber-700 underline hover:text-amber-900"
          >
            Verify on Blockstream
          </a>
        )}
      </div>
    </section>
  );
};

// ─── Section 4: Auditor Deep Dive ────────────────────────────────────────────

const AuditorDeepDive: React.FC<{
  controls: PortalControl[];
  chain: ChainVerification | null;
  incidents: PortalIncident[];
  siteId: string;
  token: string | null;
  navigate: ReturnType<typeof useNavigate>;
}> = ({ controls, chain, incidents, siteId, token, navigate }) => {
  const [open, setOpen] = useState(false);

  const verifyUrl = token
    ? `/portal/site/${siteId}/verify?token=${token}`
    : `/portal/site/${siteId}/verify`;

  const packetUrl = token
    ? `/api/portal/site/${siteId}/report/monthly?token=${token}`
    : `/api/portal/site/${siteId}/report/monthly`;

  return (
    <section className="bg-white rounded-2xl border border-slate-200 shadow-sm mb-8 auditor-deep-dive">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between p-6 text-left print-hide"
      >
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Auditor & Insurance Details</h2>
          <p className="text-sm text-slate-500 mt-1">
            Full HIPAA control mapping, evidence chain integrity, and incident log
          </p>
        </div>
        <svg
          className={`w-5 h-5 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      <div className={`${open ? '' : 'hidden'} auditor-content border-t border-slate-200`}>
        {/* 4a: HIPAA Control Table */}
        <div className="p-6">
          <h3 className="font-semibold text-slate-900 mb-4">HIPAA Control Mapping</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left">
                  <th className="py-2 pr-4 text-slate-500 font-medium">HIPAA Code</th>
                  <th className="py-2 pr-4 text-slate-500 font-medium">Section</th>
                  <th className="py-2 pr-4 text-slate-500 font-medium">Control</th>
                  <th className="py-2 pr-4 text-slate-500 font-medium">Status</th>
                  <th className="py-2 pr-4 text-slate-500 font-medium">Severity</th>
                  <th className="py-2 pr-4 text-slate-500 font-medium">Last Checked</th>
                  <th className="py-2 text-slate-500 font-medium">Scope</th>
                </tr>
              </thead>
              <tbody>
                {controls.map((c) => {
                  const statusBg = c.status === 'pass' ? 'bg-green-100 text-green-800'
                    : c.status === 'warn' ? 'bg-orange-100 text-orange-800'
                    : 'bg-red-100 text-red-800';
                  return (
                    <tr key={c.rule_id} className="border-b border-slate-100 even:bg-slate-50">
                      <td className="py-2 pr-4 font-mono text-xs">{c.hipaa_controls?.join(', ') || '--'}</td>
                      <td className="py-2 pr-4">{c.hipaa_section || '--'}</td>
                      <td className="py-2 pr-4 font-medium">{c.name}</td>
                      <td className="py-2 pr-4">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${statusBg}`}>
                          {c.status.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-2 pr-4 capitalize">{c.severity}</td>
                      <td className="py-2 pr-4 text-slate-500">
                        {c.checked_at ? new Date(c.checked_at).toLocaleString() : '--'}
                      </td>
                      <td className="py-2 text-slate-600">{c.scope_summary}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* 4b: Evidence Chain Integrity */}
        {chain && (
          <div className="p-6 border-t border-slate-200">
            <h3 className="font-semibold text-slate-900 mb-4">Evidence Chain Integrity</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <span className="text-2xl font-bold text-slate-900">{chain.chain_length.toLocaleString()}</span>
                <p className="text-xs text-slate-500 mt-1">Evidence Bundles</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <span className={`text-2xl font-bold ${chain.status === 'valid' ? 'text-green-600' : 'text-red-600'}`}>
                  {chain.status === 'valid' ? 'Valid' : 'Broken'}
                </span>
                <p className="text-xs text-slate-500 mt-1">Chain Status</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <span className="text-2xl font-bold text-green-600">{chain.signatures_valid.toLocaleString()}</span>
                <p className="text-xs text-slate-500 mt-1">Valid Signatures</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-4 text-center">
                <span className="text-sm text-slate-700">
                  {chain.first_timestamp ? new Date(chain.first_timestamp).toLocaleDateString() : '--'}
                  {' \u2013 '}
                  {chain.last_timestamp ? new Date(chain.last_timestamp).toLocaleDateString() : '--'}
                </span>
                <p className="text-xs text-slate-500 mt-1">Evidence Period</p>
              </div>
            </div>
            <p className="text-sm text-slate-600">
              Each compliance scan produces a cryptographically signed evidence bundle. Bundles are hash-chained
              (SHA-256) so any tampering breaks the chain. Signatures use Ed25519 keys unique to each appliance.
            </p>
          </div>
        )}

        {/* 4c: Incident Log */}
        <div className="p-6 border-t border-slate-200">
          <h3 className="font-semibold text-slate-900 mb-4">Recent Incidents (30 days)</h3>
          <IncidentList incidents={incidents} />
        </div>

        {/* 4d: Actions */}
        <div className="p-6 border-t border-slate-200 flex flex-wrap gap-3 print-hide">
          <button
            onClick={() => window.print()}
            className="px-4 py-2 bg-slate-900 text-white rounded-lg hover:bg-slate-800 transition text-sm font-medium"
          >
            Print Scorecard
          </button>
          <a
            href={packetUrl}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition text-sm font-medium"
          >
            Download Compliance Packet
          </a>
          <button
            onClick={() => navigate(verifyUrl)}
            className="px-4 py-2 bg-white border border-slate-300 text-slate-700 rounded-lg hover:bg-slate-50 transition text-sm font-medium"
          >
            View Evidence Chain
          </button>
        </div>
      </div>
    </section>
  );
};

// ─── Print Stylesheet ────────────────────────────────────────────────────────

const PrintStyles = () => (
  <style>{`
    @media print {
      .auditor-content { display: block !important; }
      .print-hide { display: none !important; }
      body, .min-h-screen { background: white !important; }
      section, .rounded-xl, .rounded-2xl { break-inside: avoid; }
      * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    }
  `}</style>
);

// ─── Main Component ──────────────────────────────────────────────────────────

export const PortalScorecard: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { siteId } = useParams<{ siteId: string }>();
  const token = searchParams.get('token');

  const [data, setData] = useState<PortalData | null>(null);
  const [blockchain, setBlockchain] = useState<BlockchainStatus | null>(null);
  const [chain, setChain] = useState<ChainVerification | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!siteId) {
      setError('Invalid portal URL.');
      setLoading(false);
      return;
    }

    const portalUrl = token
      ? `/api/portal/site/${siteId}?token=${token}`
      : `/api/portal/site/${siteId}`;

    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;

    Promise.all([
      fetch(portalUrl, { credentials: 'include' })
        .then(r => {
          if (r.status === 403) {
            navigate(`/portal/site/${siteId}/login`, { replace: true });
            throw new Error('redirect');
          }
          if (!r.ok) throw new Error('Failed to load scorecard.');
          return r.json();
        }),
      fetch(`/api/evidence/sites/${siteId}/blockchain-status`, { credentials: 'include', headers })
        .then(r => r.ok ? r.json() : null),
      fetch(`/api/evidence/sites/${siteId}/verify-chain`, { credentials: 'include', headers })
        .then(r => r.ok ? r.json() : null),
    ])
      .then(([portalData, bcData, chainData]) => {
        setData(portalData);
        if (bcData) setBlockchain(bcData);
        if (chainData) setChain(chainData);
      })
      .catch(e => {
        if (e.message !== 'redirect') setError(e.message);
      })
      .finally(() => setLoading(false));
  }, [siteId, token, navigate]);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-slate-600">Loading compliance scorecard...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-lg p-8 text-center">
          <h1 className="text-xl font-semibold text-slate-900 mb-2">Unable to load scorecard</h1>
          <p className="text-slate-600 mb-6">{error}</p>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const dashboardUrl = token
    ? `/portal/site/${siteId}/dashboard?token=${token}`
    : `/portal/site/${siteId}/dashboard`;

  return (
    <div className="min-h-screen bg-slate-50">
      <PrintStyles />

      {/* Header */}
      <header className="bg-white border-b shadow-sm print-hide">
        <div className="max-w-5xl mx-auto px-6 py-4 flex justify-between items-center">
          <div>
            <h1 className="text-xl font-bold text-slate-900">Compliance Scorecard</h1>
            <p className="text-sm text-slate-500">HIPAA compliance summary for {data.site.name}</p>
          </div>
          <button
            onClick={() => navigate(dashboardUrl)}
            className="px-4 py-2 text-sm text-slate-600 hover:text-blue-700 hover:bg-blue-50 rounded-lg transition"
          >
            Back to Dashboard
          </button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8">
        <HeroScore site={data.site} kpis={data.kpis} generatedAt={data.generated_at} />
        <StatusCards controls={data.controls} />
        <BlockchainTrustStamp data={blockchain} />
        <AuditorDeepDive
          controls={data.controls}
          chain={chain}
          incidents={data.incidents}
          siteId={siteId!}
          token={token}
          navigate={navigate}
        />

        {/* Footer */}
        <footer className="mt-8 pt-6 border-t border-slate-200 text-center">
          <p className="text-xs text-slate-400 max-w-2xl mx-auto">
            This scorecard is generated from real-time compliance monitoring data. Evidence bundles are
            cryptographically signed and hash-chained. Bitcoin blockchain anchors provide independent,
            tamper-proof verification of evidence timestamps. For questions, contact your compliance administrator.
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

export default PortalScorecard;

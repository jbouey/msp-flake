/**
 * PortalAuditPackage — #150 wave 3/3.
 *
 * Client-facing audit package UI. Route: /client/audit-package.
 * Psychology-first design per Session 206 round-table:
 *   - "Your package is ready" framing (not "Generate")
 *   - 3-metric hero (counts, not percentages)
 *   - Pre-validated trust signals (green checks)
 *   - Named contact (audit-team@osiriscare.net) not support
 *   - Audit log visible — client sees exactly who downloaded when
 *   - Optional "send to auditor" with email tracking
 *
 * Backend: /api/client/audit-package/{generate,list,/{id}/download,...}
 */

import React, { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { csrfHeaders } from '../utils/csrf';

interface AuditPackageRow {
  package_id: string;
  site_id: string;
  period_start: string;
  period_end: string;
  generated_at: string;
  generated_by: string;
  bundles_count: number;
  packets_count: number;
  zip_sha256: string;
  zip_size_bytes: number;
  framework: string;
  download_count: number;
  last_downloaded_at: string | null;
  delivered_to_email: string | null;
  delivered_at: string | null;
}

interface PackageListResponse {
  count: number;
  packages: AuditPackageRow[];
}

interface AuditLogEvent {
  download_id: number;
  downloaded_at: string;
  downloader: string;
  ip_address: string | null;
  user_agent: string | null;
  referrer: string | null;
}

const FRAMEWORK_LABELS: Record<string, string> = {
  hipaa: 'HIPAA Security Rule',
  soc2: 'SOC 2 Trust Services',
  pci_dss: 'PCI DSS 4.0',
  nist_csf: 'NIST CSF',
  cis: 'CIS Controls v8',
  iso_27001: 'ISO 27001:2022',
  nist_800_171: 'NIST SP 800-171',
};

function relTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const diff = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function bytesHuman(n: number): string {
  if (n < 1024) return `${n} B`;
  const k = 1024;
  const units = ['KB', 'MB', 'GB'];
  let i = -1;
  let v = n;
  while (v >= k && i < units.length - 1) { v /= k; i += 1; }
  return `${v.toFixed(1)} ${units[Math.max(0, i)]}`;
}

function defaultQuarter(): { start: string; end: string } {
  const now = new Date();
  const q = Math.floor(now.getUTCMonth() / 3);
  const startMonth = q * 3;
  const start = new Date(Date.UTC(now.getUTCFullYear(), startMonth, 1));
  const end = new Date(Date.UTC(now.getUTCFullYear(), startMonth + 3, 0));
  return {
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  };
}

interface Props {
  siteId?: string;
}

export const PortalAuditPackage: React.FC<Props> = ({ siteId: siteIdProp }) => {
  const params = useParams<{ siteId: string }>();
  const siteId = siteIdProp || params.siteId || '';
  if (!siteId) {
    return <div className="p-6 text-rose-600">No site selected.</div>;
  }
  const [packages, setPackages] = useState<AuditPackageRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sendingPkg, setSendingPkg] = useState<string | null>(null);
  const [auditLog, setAuditLog] = useState<Record<string, AuditLogEvent[]>>({});
  const q = useMemo(defaultQuarter, []);
  const [periodStart, setPeriodStart] = useState(q.start);
  const [periodEnd, setPeriodEnd] = useState(q.end);
  const [framework, setFramework] = useState('hipaa');
  const [auditorEmail, setAuditorEmail] = useState('');

  const loadList = () => {
    fetch(`/api/client/audit-package/list?site_id=${encodeURIComponent(siteId)}`, {
      credentials: 'include',
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: PackageListResponse) => { setPackages(d.packages); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadList(); }, [siteId]);

  // Lazy-load Calendly's embed script only when this page is visited —
  // no page-weight cost on unrelated portal pages.
  useEffect(() => {
    if (document.querySelector('script[data-calendly]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://assets.calendly.com/assets/external/widget.css';
    document.head.appendChild(link);
    const s = document.createElement('script');
    s.src = 'https://assets.calendly.com/assets/external/widget.js';
    s.async = true;
    s.dataset.calendly = '1';
    document.body.appendChild(s);
  }, []);

  const generate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const r = await fetch('/api/client/audit-package/generate', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({
          site_id: siteId,
          period_start: periodStart,
          period_end: periodEnd,
          framework,
        }),
      });
      if (!r.ok) throw new Error(`Generate failed: HTTP ${r.status}`);
      await r.json();
      loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  };

  const download = (pkg: AuditPackageRow) => {
    window.location.href = `/api/client/audit-package/${pkg.package_id}/download`;
  };

  const sendToAuditor = async (pkg: AuditPackageRow) => {
    if (!auditorEmail) return;
    setSendingPkg(pkg.package_id);
    try {
      await fetch(`/api/client/audit-package/${pkg.package_id}/send`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ auditor_email: auditorEmail }),
      });
      setAuditorEmail('');
      loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSendingPkg(null);
    }
  };

  const toggleAuditLog = async (packageId: string) => {
    if (auditLog[packageId]) {
      setAuditLog((m) => { const c = { ...m }; delete c[packageId]; return c; });
      return;
    }
    const r = await fetch(`/api/client/audit-package/${packageId}/audit-log`, {
      credentials: 'include',
    });
    if (r.ok) {
      const d = await r.json();
      setAuditLog((m) => ({ ...m, [packageId]: d.events }));
    }
  };

  const mostRecent = packages[0] || null;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 py-8 px-4">
      <div className="max-w-5xl mx-auto space-y-6">

        {/* Header */}
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Audit packages</h1>
          <p className="text-sm text-slate-600 mt-1">
            One-click deliverable to hand to your HIPAA, SOC 2, or PCI auditor.
            Cryptographically signed. Byte-identical re-runs for 7 years.
          </p>
        </div>

        {/* "Your package is ready" hero — shown when at least one exists */}
        {mostRecent && (
          <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-emerald-700 text-sm font-medium">
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7"/>
                  </svg>
                  Your most recent audit package is ready
                </div>
                <h2 className="text-lg font-semibold text-slate-900 mt-1">
                  {mostRecent.period_start} → {mostRecent.period_end}
                </h2>
                <div className="text-xs text-slate-500 mt-1">
                  {FRAMEWORK_LABELS[mostRecent.framework] || mostRecent.framework} ·
                  generated {relTime(mostRecent.generated_at)} · {bytesHuman(mostRecent.zip_size_bytes)}
                </div>
              </div>
              <button
                onClick={() => download(mostRecent)}
                className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2.5 rounded-md font-medium shadow-sm flex items-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                Download ZIP
              </button>
            </div>

            {/* Three trust metrics */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-5">
              <div className="bg-white border border-emerald-100 rounded-md p-3">
                <div className="text-2xl font-semibold text-emerald-700 tabular-nums">{mostRecent.bundles_count.toLocaleString()}</div>
                <div className="text-xs text-slate-500 uppercase tracking-wide">evidence bundles</div>
              </div>
              <div className="bg-white border border-emerald-100 rounded-md p-3">
                <div className="text-2xl font-semibold text-emerald-700 tabular-nums">{mostRecent.packets_count}</div>
                <div className="text-xs text-slate-500 uppercase tracking-wide">monthly packets</div>
              </div>
              <div className="bg-white border border-emerald-100 rounded-md p-3">
                <div className="text-2xl font-semibold text-emerald-700 font-mono">{mostRecent.zip_sha256.slice(0, 12)}…</div>
                <div className="text-xs text-slate-500 uppercase tracking-wide">sha256 (verify integrity)</div>
              </div>
            </div>

            {/* Send to auditor */}
            <div className="mt-5 flex items-center gap-2">
              <input
                type="email"
                placeholder="your-auditor@firm.com"
                value={auditorEmail}
                onChange={(e) => setAuditorEmail(e.target.value)}
                className="flex-1 px-3 py-2 border border-slate-300 rounded-md text-sm"
              />
              <button
                onClick={() => sendToAuditor(mostRecent)}
                disabled={!auditorEmail || sendingPkg === mostRecent.package_id}
                className="px-4 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-md text-sm font-medium disabled:opacity-40"
              >
                {sendingPkg === mostRecent.package_id ? 'Recording…' : 'Share link'}
              </button>
            </div>
            {mostRecent.delivered_to_email && (
              <div className="mt-2 text-xs text-slate-600">
                Delivered to <span className="font-mono">{mostRecent.delivered_to_email}</span>
                {' · '}{relTime(mostRecent.delivered_at)}
              </div>
            )}

            {/* Walkthrough call — Calendly popup. Psychology: punt the
                hard auditor questions back to US. Revenue hook. */}
            <div className="mt-4 pt-4 border-t border-emerald-100 flex items-center justify-between">
              <div className="text-sm text-slate-700">
                Need help walking your auditor through this package?
              </div>
              <button
                onClick={() => {
                  const calendlyUrl = 'https://calendly.com/osiriscare/audit-walkthrough';
                  if ((window as unknown as { Calendly?: { initPopupWidget: (o: { url: string }) => void } }).Calendly) {
                    (window as unknown as { Calendly: { initPopupWidget: (o: { url: string }) => void } })
                      .Calendly.initPopupWidget({ url: calendlyUrl });
                  } else {
                    window.open(calendlyUrl, '_blank', 'noopener,noreferrer');
                  }
                }}
                className="px-4 py-2 bg-white border border-emerald-300 text-emerald-700 hover:bg-emerald-100 rounded-md text-sm font-medium"
              >
                Schedule 15-min walkthrough →
              </button>
            </div>
          </div>
        )}

        {/* Generate new package */}
        <div className="bg-white border border-slate-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-slate-900 mb-3">Generate a new audit package</h2>
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Period start</label>
              <input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Period end</label>
              <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Framework</label>
              <select value={framework} onChange={(e) => setFramework(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm">
                {Object.entries(FRAMEWORK_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </div>
            <div className="flex items-end">
              <button
                onClick={generate}
                disabled={generating}
                className="w-full bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md font-medium disabled:opacity-40"
              >
                {generating ? 'Generating…' : 'Generate'}
              </button>
            </div>
          </div>
          <p className="text-[11px] text-slate-500 mt-3">
            Generation snapshots every evidence bundle in the period. Typical quarter takes ≤30s.
            Questions? Reach <a className="text-blue-600 hover:underline" href="mailto:audit-team@osiriscare.net">audit-team@osiriscare.net</a> — a named engineer, not a ticket queue.
          </p>
        </div>

        {error && (
          <div className="bg-rose-50 border border-rose-200 text-rose-700 px-4 py-3 rounded-md text-sm">
            {error}
          </div>
        )}

        {/* History */}
        {packages.length > 1 && (
          <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-200 bg-slate-50">
              <h2 className="text-sm font-semibold text-slate-900">Package history</h2>
            </div>
            <table className="w-full text-sm">
              <thead className="text-[11px] uppercase tracking-wide text-slate-500 border-b border-slate-200">
                <tr>
                  <th className="py-2 px-4 text-left font-medium">Period</th>
                  <th className="py-2 px-4 text-left font-medium">Framework</th>
                  <th className="py-2 px-4 text-right font-medium">Bundles</th>
                  <th className="py-2 px-4 text-right font-medium">Downloads</th>
                  <th className="py-2 px-4 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {packages.slice(1).map((pkg) => (
                  <React.Fragment key={pkg.package_id}>
                    <tr>
                      <td className="py-2 px-4 text-slate-700">{pkg.period_start} → {pkg.period_end}</td>
                      <td className="py-2 px-4 text-slate-600 text-xs">{FRAMEWORK_LABELS[pkg.framework] || pkg.framework}</td>
                      <td className="py-2 px-4 text-right tabular-nums">{pkg.bundles_count.toLocaleString()}</td>
                      <td className="py-2 px-4 text-right tabular-nums">{pkg.download_count}</td>
                      <td className="py-2 px-4 text-right">
                        <button onClick={() => download(pkg)} className="text-blue-600 hover:underline text-xs mr-3">Download</button>
                        <button onClick={() => toggleAuditLog(pkg.package_id)} className="text-slate-600 hover:text-slate-800 text-xs">
                          {auditLog[pkg.package_id] ? 'Hide log' : 'Who fetched'}
                        </button>
                      </td>
                    </tr>
                    {auditLog[pkg.package_id] && (
                      <tr className="bg-slate-50">
                        <td colSpan={5} className="px-4 py-3">
                          <div className="text-[11px] text-slate-500 mb-2">Download + delivery events</div>
                          {auditLog[pkg.package_id].length === 0 ? (
                            <div className="text-xs text-slate-400 italic">No events.</div>
                          ) : (
                            <ul className="text-xs text-slate-700 space-y-1">
                              {auditLog[pkg.package_id].map((e) => (
                                <li key={e.download_id} className="flex items-start gap-3">
                                  <span className="text-slate-400 tabular-nums">{relTime(e.downloaded_at)}</span>
                                  <span className="font-mono">{e.downloader}</span>
                                  {e.ip_address && <span className="text-slate-400">from {e.ip_address}</span>}
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Empty state */}
        {!loading && packages.length === 0 && (
          <div className="bg-white border border-slate-200 rounded-lg p-8 text-center">
            <div className="text-slate-600 text-sm">
              No audit packages yet. Use the form above to generate one for the current quarter.
            </div>
          </div>
        )}

        <div className="text-[11px] text-slate-400 text-center pt-4">
          Packages retained for 7 years per HIPAA §164.316(b)(2)(i) minimum.
          Re-runs are cryptographically proven to produce identical content.
        </div>

      </div>
    </div>
  );
};

export default PortalAuditPackage;

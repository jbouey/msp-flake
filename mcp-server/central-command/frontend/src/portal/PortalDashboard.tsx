import React, { useEffect, useState } from 'react';
import { useSearchParams, useParams } from 'react-router-dom';
import { KPICard, ControlGrid, IncidentList, EvidenceDownloads } from './components';

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

interface PortalBundle {
  bundle_id: string;
  bundle_type: string;
  generated_at: string;
  size_bytes: number;
}

interface PortalData {
  site: PortalSite;
  kpis: PortalKPIs;
  controls: PortalControl[];
  incidents: PortalIncident[];
  evidence_bundles: PortalBundle[];
  generated_at: string;
}

const LoadingSkeleton: React.FC = () => (
  <div className="min-h-screen bg-gray-50 animate-pulse">
    <div className="bg-white border-b px-6 py-4">
      <div className="max-w-7xl mx-auto">
        <div className="h-8 w-64 bg-gray-200 rounded mb-2" />
        <div className="h-4 w-48 bg-gray-200 rounded" />
      </div>
    </div>
    <main className="max-w-7xl mx-auto px-6 py-8">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-24 bg-gray-200 rounded-xl" />
        ))}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
          <div key={i} className="h-48 bg-gray-200 rounded-xl" />
        ))}
      </div>
    </main>
  </div>
);

const ErrorState: React.FC<{ message: string }> = ({ message }) => (
  <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
    <div className="max-w-md w-full bg-white rounded-2xl shadow-lg p-8 text-center">
      <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
        <span className="text-3xl">🔒</span>
      </div>
      <h1 className="text-xl font-semibold text-gray-900 mb-2">Access Denied</h1>
      <p className="text-gray-600 mb-6">{message}</p>
      <p className="text-sm text-gray-400">
        If you believe this is an error, please contact your administrator.
      </p>
    </div>
  </div>
);

export const PortalDashboard: React.FC = () => {
  const [searchParams] = useSearchParams();
  const { siteId } = useParams<{ siteId: string }>();
  const token = searchParams.get('token');

  const [data, setData] = useState<PortalData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setError('Missing portal access token. Please use the link provided by your administrator.');
      setLoading(false);
      return;
    }

    if (!siteId) {
      setError('Invalid portal URL.');
      setLoading(false);
      return;
    }

    fetch(`/api/portal/site/${siteId}?token=${token}`)
      .then((r) => {
        if (!r.ok) {
          throw new Error('Invalid or expired portal link. Please request a new link from your administrator.');
        }
        return r.json();
      })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [siteId, token]);

  if (loading) return <LoadingSkeleton />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  const getKPIStatus = (value: number, thresholds: { pass: number; warn: number }): 'pass' | 'warn' | 'fail' => {
    if (value >= thresholds.pass) return 'pass';
    if (value >= thresholds.warn) return 'warn';
    return 'fail';
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{data.site.name}</h1>
            <p className="text-sm text-gray-500">
              HIPAA Compliance Dashboard • Last updated:{' '}
              {data.site.last_checkin
                ? new Date(data.site.last_checkin).toLocaleString()
                : 'Never'}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span
              className={`px-3 py-1.5 rounded-full text-sm font-medium ${
                data.site.status === 'online'
                  ? 'bg-green-100 text-green-800'
                  : 'bg-red-100 text-red-800'
              }`}
            >
              {data.site.status === 'online' ? '● Online' : '● Offline'}
            </span>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8">
        {/* KPI Section */}
        <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <KPICard
            label="Compliance Score"
            value={data.kpis.compliance_pct ?? 0}
            unit="%"
            status={getKPIStatus(data.kpis.compliance_pct ?? 0, { pass: 95, warn: 80 })}
          />
          <KPICard
            label="Patch MTTR"
            value={data.kpis.patch_mttr_hours ?? 0}
            unit=" hrs"
            status={(data.kpis.patch_mttr_hours ?? 0) < 24 ? 'pass' : (data.kpis.patch_mttr_hours ?? 0) < 72 ? 'warn' : 'fail'}
          />
          <KPICard
            label="MFA Coverage"
            value={data.kpis.mfa_coverage_pct ?? 0}
            unit="%"
            status={(data.kpis.mfa_coverage_pct ?? 0) === 100 ? 'pass' : 'warn'}
          />
          <KPICard
            label="Auto-Fixes (24h)"
            value={data.kpis.auto_fixes_24h ?? 0}
            unit=""
            status="pass"
          />
        </section>

        {/* Controls Summary Bar */}
        <section className="mb-6">
          <div className="bg-white rounded-xl border border-gray-200 p-4 flex items-center justify-between">
            <div className="flex items-center gap-6">
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-green-500" />
                <span className="text-sm text-gray-600">
                  <strong className="text-gray-900">{data.kpis.controls_passing ?? 0}</strong> Passing
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-orange-500" />
                <span className="text-sm text-gray-600">
                  <strong className="text-gray-900">{data.kpis.controls_warning ?? 0}</strong> Warning
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-red-500" />
                <span className="text-sm text-gray-600">
                  <strong className="text-gray-900">{data.kpis.controls_failing ?? 0}</strong> Failing
                </span>
              </div>
            </div>
            <span className="text-sm text-gray-400">
              8 Core HIPAA Controls
            </span>
          </div>
        </section>

        {/* Controls Section */}
        <section className="mb-8">
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Control Status</h2>
          <ControlGrid controls={data.controls} />
        </section>

        {/* Two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Recent Incidents */}
          <section>
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Recent Activity</h2>
            <IncidentList incidents={data.incidents} />
          </section>

          {/* Evidence Downloads */}
          <section>
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Evidence & Reports</h2>
            <EvidenceDownloads
              bundles={data.evidence_bundles}
              siteId={siteId!}
              token={token!}
            />
          </section>
        </div>

        {/* Footer */}
        <footer className="mt-12 pt-8 border-t border-gray-200 text-center">
          <p className="text-sm text-gray-500 max-w-2xl mx-auto">
            This report contains system metadata only. No Protected Health Information (PHI)
            is processed, stored, or transmitted by the compliance monitoring system.
          </p>
          <p className="mt-4 text-sm text-gray-400">
            Questions?{' '}
            <a
              href="mailto:support@osiriscare.net"
              className="text-blue-600 hover:underline"
            >
              support@osiriscare.net
            </a>
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

export default PortalDashboard;

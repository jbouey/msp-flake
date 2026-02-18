import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useClient } from './ClientContext';

interface MonthlyReport {
  id: string;
  month: string;
  year: number;
  generated_at: string;
  total_checks: number;
  passed_checks: number;
  failed_checks: number;
  compliance_score: number;
}

interface SiteScore {
  site_id: string;
  clinic_name: string;
  score: number;
  passed: number;
  failed: number;
  total: number;
}

interface ComplianceCheck {
  site_id: string;
  check_type: string;
  result: string;
  hipaa_control: string;
  hostname: string;
  checked_at: string;
}

interface Snapshot {
  generated_at: string;
  overall_score: number;
  sites: SiteScore[];
  controls: { passed: number; failed: number; warnings: number; total: number };
  healing: { total: number; auto_healed: number; pending: number };
  checks: ComplianceCheck[];
}

export const ClientReports: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, isLoading } = useClient();

  const [activeTab, setActiveTab] = useState<'current' | 'monthly'>('current');
  const [reports, setReports] = useState<MonthlyReport[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/client/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchReports();
      fetchSnapshot();
    }
  }, [isAuthenticated]);

  const fetchReports = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/client/reports/monthly', { credentials: 'include' });
      if (response.ok) {
        const data = await response.json();
        setReports(data.reports || []);
      } else {
        setError('Failed to load reports');
      }
    } catch (e) {
      console.error('Failed to fetch reports:', e);
      setError('Failed to load reports');
    } finally {
      setLoading(false);
    }
  };

  const fetchSnapshot = async () => {
    setSnapshotLoading(true);
    try {
      const response = await fetch('/api/client/reports/current', { credentials: 'include' });
      if (response.ok) {
        setSnapshot(await response.json());
      }
    } catch (e) {
      console.error('Failed to fetch snapshot:', e);
    } finally {
      setSnapshotLoading(false);
    }
  };

  const handleDownload = async (month: string) => {
    try {
      const response = await fetch(`/api/client/reports/monthly/${month}`, {
        credentials: 'include',
      });
      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `compliance-report-${month}.pdf`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      }
    } catch (e) {
      console.error('Failed to download report:', e);
    }
  };

  const getScoreColor = (score: number) => {
    if (score >= 90) return 'text-green-600';
    if (score >= 70) return 'text-yellow-600';
    return 'text-red-600';
  };

  const getScoreBg = (score: number) => {
    if (score >= 90) return 'bg-green-100 text-green-700';
    if (score >= 70) return 'bg-yellow-100 text-yellow-700';
    return 'bg-red-100 text-red-700';
  };

  const getResultBadge = (result: string) => {
    if (result === 'pass') return 'bg-green-100 text-green-700';
    if (result === 'fail') return 'bg-red-100 text-red-700';
    return 'bg-yellow-100 text-yellow-700';
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="w-12 h-12 border-4 border-teal-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50/80 page-enter">
      {/* Header */}
      <header className="sticky top-0 z-30 border-b border-slate-200/60" style={{ background: 'rgba(255,255,255,0.82)', backdropFilter: 'blur(20px) saturate(180%)', WebkitBackdropFilter: 'blur(20px) saturate(180%)' }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-14">
            <div className="flex items-center gap-4">
              <Link to="/client/dashboard" className="p-2 text-slate-500 hover:text-teal-600 rounded-lg hover:bg-teal-50">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </Link>
              <h1 className="text-lg font-semibold text-slate-900">Compliance Reports</h1>
            </div>
          </div>
        </div>
      </header>

      {/* Tabs */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-6">
        <div className="flex gap-2">
          <button
            onClick={() => setActiveTab('current')}
            className={`px-4 py-2 text-sm font-medium rounded-xl transition-all ${activeTab === 'current' ? 'text-white shadow-sm' : 'text-slate-600 bg-white border border-slate-200 hover:bg-slate-50'}`}
            style={activeTab === 'current' ? { background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' } : {}}
          >
            Current Compliance
          </button>
          <button
            onClick={() => setActiveTab('monthly')}
            className={`px-4 py-2 text-sm font-medium rounded-xl transition-all ${activeTab === 'monthly' ? 'text-white shadow-sm' : 'text-slate-600 bg-white border border-slate-200 hover:bg-slate-50'}`}
            style={activeTab === 'monthly' ? { background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' } : {}}
          >
            Monthly Reports
          </button>
        </div>
      </div>

      {/* Main */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {activeTab === 'current' ? (
          /* Current Compliance Snapshot */
          snapshotLoading ? (
            <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-8 text-center">
              <div className="w-8 h-8 border-4 border-teal-500 border-t-transparent rounded-full animate-spin mx-auto" />
            </div>
          ) : !snapshot ? (
            <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-8 text-center text-slate-500">
              Unable to load compliance data
            </div>
          ) : (
            <div className="space-y-6">
              {/* Score Banner */}
              <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-sm font-medium text-slate-500 uppercase tracking-wider">Current Compliance Score</h2>
                    <p className={`text-5xl font-bold mt-2 tabular-nums ${getScoreColor(snapshot.overall_score)}`}>
                      {snapshot.overall_score.toFixed(1)}%
                    </p>
                    <p className="text-sm text-slate-500 mt-1">
                      Real-time snapshot as of {new Date(snapshot.generated_at).toLocaleString()}
                    </p>
                  </div>
                  <div className="flex gap-6">
                    <div className="text-center">
                      <p className="text-2xl font-bold text-green-600 tabular-nums">{snapshot.controls.passed}</p>
                      <p className="text-xs text-slate-500">Passed</p>
                    </div>
                    <div className="text-center">
                      <p className="text-2xl font-bold text-red-600 tabular-nums">{snapshot.controls.failed}</p>
                      <p className="text-xs text-slate-500">Failed</p>
                    </div>
                    <div className="text-center">
                      <p className="text-2xl font-bold text-yellow-600 tabular-nums">{snapshot.controls.warnings}</p>
                      <p className="text-xs text-slate-500">Warnings</p>
                    </div>
                    <div className="text-center">
                      <p className="text-2xl font-bold text-teal-600 tabular-nums">{snapshot.healing.auto_healed}</p>
                      <p className="text-xs text-slate-500">Auto-Healed</p>
                    </div>
                  </div>
                  <button
                    onClick={fetchSnapshot}
                    className="px-4 py-2 text-white rounded-xl hover:brightness-110 transition-all flex items-center gap-2"
                    style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                    Refresh
                  </button>
                </div>
              </div>

              {/* Per-Site Scores */}
              {snapshot.sites.length > 0 && (
                <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
                  <div className="p-4 border-b border-slate-100">
                    <h3 className="font-semibold text-slate-900">Site Breakdown</h3>
                  </div>
                  <div className="divide-y divide-slate-100">
                    {snapshot.sites.map((site) => (
                      <div key={site.site_id} className="p-4 flex items-center justify-between hover:bg-teal-50/30">
                        <div>
                          <p className="font-medium text-slate-900">{site.clinic_name}</p>
                          <p className="text-xs text-slate-500">{site.site_id}</p>
                        </div>
                        <div className="flex items-center gap-6">
                          <span className="text-sm text-slate-600">{site.passed}/{site.total} passed</span>
                          <span className={`px-3 py-1 text-sm font-semibold rounded-full tabular-nums ${getScoreBg(site.score)}`}>
                            {site.score.toFixed(1)}%
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Check Details */}
              {snapshot.checks.length > 0 && (
                <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
                  <div className="p-4 border-b border-slate-100">
                    <h3 className="font-semibold text-slate-900">All Compliance Checks ({snapshot.checks.length})</h3>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-slate-50 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                          <th className="px-4 py-3">Check Type</th>
                          <th className="px-4 py-3">HIPAA Control</th>
                          <th className="px-4 py-3">Host</th>
                          <th className="px-4 py-3">Result</th>
                          <th className="px-4 py-3">Last Checked</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {snapshot.checks.map((check, i) => (
                          <tr key={i} className="hover:bg-slate-50/50">
                            <td className="px-4 py-3 font-medium text-slate-900">{check.check_type}</td>
                            <td className="px-4 py-3 text-slate-600">{check.hipaa_control || '-'}</td>
                            <td className="px-4 py-3 text-slate-600 font-mono text-xs">{check.hostname || '-'}</td>
                            <td className="px-4 py-3">
                              <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${getResultBadge(check.result)}`}>
                                {check.result}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-slate-500">
                              {check.checked_at ? new Date(check.checked_at).toLocaleString() : '-'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )
        ) : (
          /* Monthly Reports Tab */
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
            {loading ? (
              <div className="p-8 text-center">
                <div className="w-8 h-8 border-4 border-teal-500 border-t-transparent rounded-full animate-spin mx-auto" />
              </div>
            ) : error ? (
              <div className="p-8 text-center text-red-500">{error}</div>
            ) : reports.length === 0 ? (
              <div className="p-8 text-center text-slate-500">
                <svg className="w-12 h-12 mx-auto mb-4 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <p>No monthly reports available yet</p>
                <p className="text-sm mt-2">Reports are generated at the end of each month</p>
              </div>
            ) : (
              <div className="divide-y divide-slate-200">
                {reports.map((report) => (
                  <div key={report.id} className="p-6 hover:bg-teal-50/50 flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <div className="w-12 h-12 rounded-lg bg-purple-100 flex items-center justify-center">
                        <svg className="w-6 h-6 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <div>
                        <h3 className="font-medium text-slate-900">
                          {report.month} {report.year}
                        </h3>
                        <p className="text-sm text-slate-500">
                          Generated {new Date(report.generated_at).toLocaleDateString()}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-8">
                      <div className="text-right">
                        <p className={`text-2xl font-bold ${getScoreColor(report.compliance_score)}`}>
                          <span className="tabular-nums">{report.compliance_score.toFixed(1)}%</span>
                        </p>
                        <p className="text-xs text-slate-500">Compliance Score</p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm text-slate-900">
                          {report.passed_checks}/{report.total_checks} passed
                        </p>
                        <p className="text-xs text-slate-500">{report.failed_checks} failed</p>
                      </div>
                      <button
                        onClick={() => handleDownload(report.month)}
                        className="px-4 py-2 text-white rounded-xl hover:brightness-110 transition-all flex items-center gap-2"
                        style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                        Download PDF
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
};

export default ClientReports;

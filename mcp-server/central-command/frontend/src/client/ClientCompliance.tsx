import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useClient } from './ClientContext';
import { OsirisCareLeaf } from '../components/shared';
import { SRAWizard } from './compliance/SRAWizard';
import { PolicyLibrary } from './compliance/PolicyLibrary';
import { TrainingTracker } from './compliance/TrainingTracker';
import { BAATracker } from './compliance/BAATracker';
import { IncidentResponsePlan } from './compliance/IncidentResponsePlan';
import { ContingencyPlan } from './compliance/ContingencyPlan';
import { WorkforceAccess } from './compliance/WorkforceAccess';
import { PhysicalSafeguards } from './compliance/PhysicalSafeguards';
import { OfficerDesignation } from './compliance/OfficerDesignation';
import { GapWizard } from './compliance/GapWizard';

interface Overview {
  sra: { status: string; risk_score: number | null; expires_at: string | null; findings: number };
  policies: { total: number; active: number; review_due: number };
  training: { total_employees: number; compliant: number; overdue: number };
  baas: { total: number; active: number; expiring_soon: number };
  ir_plan: { status: string; last_tested: string | null; breaches: number };
  contingency: { plans: number; all_tested: boolean };
  workforce: { active: number; pending_termination: number };
  physical: { assessed: number; compliant: number; gaps: number };
  officers: { privacy_officer: string | null; security_officer: string | null };
  gap_analysis: { completion: number; maturity_avg: number };
  overall_readiness: number;
}

type ModuleTab = 'overview' | 'sra' | 'policies' | 'training' | 'baas' | 'ir-plan' | 'contingency' | 'workforce' | 'physical' | 'officers' | 'gap-analysis';

const MODULE_CONFIG: { key: ModuleTab; label: string; icon: string; color: string }[] = [
  { key: 'sra', label: 'Risk Assessment', icon: 'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z', color: 'teal' },
  { key: 'policies', label: 'Policy Library', icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z', color: 'blue' },
  { key: 'training', label: 'Training', icon: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253', color: 'purple' },
  { key: 'baas', label: 'BAA Tracker', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4', color: 'amber' },
  { key: 'ir-plan', label: 'Incident Response', icon: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z', color: 'red' },
  { key: 'contingency', label: 'Contingency Plans', icon: 'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4', color: 'cyan' },
  { key: 'workforce', label: 'Workforce Access', icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z', color: 'indigo' },
  { key: 'physical', label: 'Physical Safeguards', icon: 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4', color: 'orange' },
  { key: 'officers', label: 'Officer Designation', icon: 'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z', color: 'emerald' },
  { key: 'gap-analysis', label: 'Gap Analysis', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z', color: 'pink' },
];

const colorMap: Record<string, { bg: string; text: string; badge: string }> = {
  teal: { bg: 'bg-teal-100', text: 'text-teal-600', badge: 'bg-teal-500' },
  blue: { bg: 'bg-blue-100', text: 'text-blue-600', badge: 'bg-blue-500' },
  purple: { bg: 'bg-purple-100', text: 'text-purple-600', badge: 'bg-purple-500' },
  amber: { bg: 'bg-amber-100', text: 'text-amber-600', badge: 'bg-amber-500' },
  red: { bg: 'bg-red-100', text: 'text-red-600', badge: 'bg-red-500' },
  cyan: { bg: 'bg-cyan-100', text: 'text-cyan-600', badge: 'bg-cyan-500' },
  indigo: { bg: 'bg-indigo-100', text: 'text-indigo-600', badge: 'bg-indigo-500' },
  orange: { bg: 'bg-orange-100', text: 'text-orange-600', badge: 'bg-orange-500' },
  emerald: { bg: 'bg-emerald-100', text: 'text-emerald-600', badge: 'bg-emerald-500' },
  pink: { bg: 'bg-pink-100', text: 'text-pink-600', badge: 'bg-pink-500' },
};

function getModuleStatus(overview: Overview, key: ModuleTab): { label: string; color: string } {
  switch (key) {
    case 'sra':
      if (overview.sra.status === 'completed') return { label: 'Completed', color: 'green' };
      if (overview.sra.status === 'in_progress') return { label: 'In Progress', color: 'yellow' };
      return { label: 'Not Started', color: 'slate' };
    case 'policies':
      if (overview.policies.review_due > 0) return { label: `${overview.policies.review_due} Due`, color: 'yellow' };
      if (overview.policies.active > 0) return { label: `${overview.policies.active} Active`, color: 'green' };
      return { label: 'Not Started', color: 'slate' };
    case 'training':
      if (overview.training.overdue > 0) return { label: `${overview.training.overdue} Overdue`, color: 'red' };
      if (overview.training.compliant > 0) return { label: 'Current', color: 'green' };
      return { label: 'Not Started', color: 'slate' };
    case 'baas':
      if (overview.baas.expiring_soon > 0) return { label: `${overview.baas.expiring_soon} Expiring`, color: 'yellow' };
      if (overview.baas.active > 0) return { label: `${overview.baas.active} Active`, color: 'green' };
      return { label: 'Not Started', color: 'slate' };
    case 'ir-plan':
      if (overview.ir_plan.status === 'active') return { label: 'Active', color: 'green' };
      if (overview.ir_plan.status === 'draft') return { label: 'Draft', color: 'yellow' };
      return { label: 'Not Started', color: 'slate' };
    case 'contingency':
      if (overview.contingency.plans > 0 && overview.contingency.all_tested) return { label: 'Tested', color: 'green' };
      if (overview.contingency.plans > 0) return { label: `${overview.contingency.plans} Plans`, color: 'yellow' };
      return { label: 'Not Started', color: 'slate' };
    case 'workforce':
      if (overview.workforce.pending_termination > 0) return { label: 'Action Needed', color: 'red' };
      if (overview.workforce.active > 0) return { label: `${overview.workforce.active} Active`, color: 'green' };
      return { label: 'Not Started', color: 'slate' };
    case 'physical':
      if (overview.physical.gaps > 0) return { label: `${overview.physical.gaps} Gaps`, color: 'yellow' };
      if (overview.physical.assessed > 0) return { label: 'Assessed', color: 'green' };
      return { label: 'Not Started', color: 'slate' };
    case 'officers':
      if (overview.officers.privacy_officer && overview.officers.security_officer) return { label: 'Designated', color: 'green' };
      if (overview.officers.privacy_officer || overview.officers.security_officer) return { label: 'Partial', color: 'yellow' };
      return { label: 'Not Started', color: 'slate' };
    case 'gap-analysis':
      if (overview.gap_analysis.completion >= 100) return { label: 'Complete', color: 'green' };
      if (overview.gap_analysis.completion > 0) return { label: `${overview.gap_analysis.completion}%`, color: 'yellow' };
      return { label: 'Not Started', color: 'slate' };
    default:
      return { label: '', color: 'slate' };
  }
}

const statusColors: Record<string, string> = {
  green: 'bg-green-100 text-green-700',
  yellow: 'bg-yellow-100 text-yellow-700',
  red: 'bg-red-100 text-red-700',
  slate: 'bg-slate-100 text-slate-600',
};

export const ClientCompliance: React.FC = () => {
  const navigate = useNavigate();
  const { user, isAuthenticated, isLoading } = useClient();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<ModuleTab>('overview');

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/client/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  useEffect(() => {
    if (isAuthenticated) fetchOverview();
  }, [isAuthenticated]);

  const fetchOverview = async () => {
    try {
      const res = await fetch('/api/client/compliance/overview', { credentials: 'include' });
      if (res.ok) {
        setOverview(await res.json());
      } else if (res.status === 401) {
        navigate('/client/login');
      }
    } catch (e) {
      console.error('Failed to fetch compliance overview:', e);
    } finally {
      setLoading(false);
    }
  };

  const getReadinessColor = (score: number) => {
    if (score >= 80) return 'text-green-600';
    if (score >= 50) return 'text-yellow-600';
    return 'text-red-600';
  };

  const getReadinessBg = (score: number) => {
    if (score >= 80) return 'bg-green-500';
    if (score >= 50) return 'bg-yellow-500';
    return 'bg-red-500';
  };

  if (isLoading || loading) {
    return (
      <div className="min-h-screen bg-slate-50/80 flex items-center justify-center">
        <div className="text-center">
          <div className="w-14 h-14 mx-auto mb-4 rounded-2xl flex items-center justify-center animate-pulse-soft" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}>
            <OsirisCareLeaf className="w-7 h-7" color="white" />
          </div>
          <p className="text-slate-500">Loading compliance center...</p>
        </div>
      </div>
    );
  }

  const renderModuleContent = () => {
    switch (activeTab) {
      case 'sra': return <SRAWizard />;
      case 'policies': return <PolicyLibrary />;
      case 'training': return <TrainingTracker />;
      case 'baas': return <BAATracker />;
      case 'ir-plan': return <IncidentResponsePlan />;
      case 'contingency': return <ContingencyPlan />;
      case 'workforce': return <WorkforceAccess />;
      case 'physical': return <PhysicalSafeguards />;
      case 'officers': return <OfficerDesignation />;
      case 'gap-analysis': return <GapWizard />;
      default: return null;
    }
  };

  return (
    <div className="min-h-screen bg-slate-50/80 page-enter">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-slate-200/60" style={{ background: 'rgba(255,255,255,0.82)', backdropFilter: 'blur(20px) saturate(180%)', WebkitBackdropFilter: 'blur(20px) saturate(180%)' }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-14">
            <div className="flex items-center gap-4">
              <button onClick={() => navigate('/client/dashboard')} className="p-2 text-slate-400 hover:text-teal-600 rounded-lg hover:bg-teal-50">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}>
                <OsirisCareLeaf className="w-5 h-5" color="white" />
              </div>
              <div>
                <h1 className="text-lg font-semibold text-slate-900">HIPAA Compliance Center</h1>
                <p className="text-sm text-slate-500">Administrative compliance documentation</p>
              </div>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {activeTab === 'overview' ? (
          <>
            {/* Readiness Score */}
            {overview && (
              <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-8 mb-8">
                <div className="flex items-center gap-6">
                  <div className="w-24 h-24 rounded-full border-4 border-slate-100 flex items-center justify-center relative">
                    <span className={`text-3xl font-bold ${getReadinessColor(overview.overall_readiness)}`}>
                      {overview.overall_readiness}%
                    </span>
                    <svg className="absolute inset-0 w-full h-full -rotate-90" viewBox="0 0 100 100">
                      <circle cx="50" cy="50" r="45" fill="none" stroke="#e2e8f0" strokeWidth="6" />
                      <circle cx="50" cy="50" r="45" fill="none" stroke={overview.overall_readiness >= 80 ? '#22c55e' : overview.overall_readiness >= 50 ? '#eab308' : '#ef4444'} strokeWidth="6" strokeLinecap="round" strokeDasharray={`${overview.overall_readiness * 2.83} 283`} />
                    </svg>
                  </div>
                  <div>
                    <h2 className="text-2xl font-bold text-slate-900">HIPAA Readiness Score</h2>
                    <p className="text-slate-500 mt-1">Composite score across all compliance modules</p>
                  </div>
                </div>
              </div>
            )}

            {/* Module Cards Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
              {MODULE_CONFIG.map((mod) => {
                const status = overview ? getModuleStatus(overview, mod.key) : { label: '', color: 'slate' };
                const colors = colorMap[mod.color];
                return (
                  <button
                    key={mod.key}
                    onClick={() => setActiveTab(mod.key)}
                    className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5 hover:border-teal-300 hover:shadow-md transition-all text-left"
                  >
                    <div className="flex items-center justify-between mb-3">
                      <div className={`w-10 h-10 ${colors.bg} rounded-lg flex items-center justify-center`}>
                        <svg className={`w-5 h-5 ${colors.text}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={mod.icon} />
                        </svg>
                      </div>
                      <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${statusColors[status.color]}`}>
                        {status.label}
                      </span>
                    </div>
                    <h3 className="font-semibold text-slate-900 text-sm">{mod.label}</h3>
                  </button>
                );
              })}
            </div>
          </>
        ) : (
          <>
            {/* Tab Navigation */}
            <div className="flex items-center gap-2 mb-6 overflow-x-auto pb-2">
              <button
                onClick={() => setActiveTab('overview')}
                className="px-3 py-1.5 text-sm font-medium rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200 whitespace-nowrap"
              >
                Overview
              </button>
              {MODULE_CONFIG.map((mod) => (
                <button
                  key={mod.key}
                  onClick={() => setActiveTab(mod.key)}
                  className={`px-3 py-1.5 text-sm font-medium rounded-lg whitespace-nowrap ${
                    activeTab === mod.key
                      ? 'bg-teal-100 text-teal-700'
                      : 'text-slate-500 hover:bg-slate-100'
                  }`}
                >
                  {mod.label}
                </button>
              ))}
            </div>

            {/* Module Content */}
            {renderModuleContent()}
          </>
        )}
      </main>
    </div>
  );
};

export default ClientCompliance;

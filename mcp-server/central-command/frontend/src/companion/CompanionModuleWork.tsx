import React, { useState, lazy, Suspense } from 'react';
import { useParams } from 'react-router-dom';
import { companionColors, MODULE_DEFS } from './companion-tokens';
import { CompanionNotes } from './CompanionNotes';
import { useCompanionAlerts, useCreateAlert, useUpdateAlert, useDeleteAlert } from './useCompanionApi';
import { Spinner } from '../components/shared';

interface ModuleAlert {
  id: string;
  module_key: string;
  status: string;
  expected_status: string;
  target_date: string;
  description?: string;
}

// Lazy import the compliance components from the client module
const SRAWizard = lazy(() => import('../client/compliance/SRAWizard').then(m => ({ default: m.SRAWizard })));
const PolicyLibrary = lazy(() => import('../client/compliance/PolicyLibrary').then(m => ({ default: m.PolicyLibrary })));
const TrainingTracker = lazy(() => import('../client/compliance/TrainingTracker').then(m => ({ default: m.TrainingTracker })));
const BAATracker = lazy(() => import('../client/compliance/BAATracker').then(m => ({ default: m.BAATracker })));
const IncidentResponsePlan = lazy(() => import('../client/compliance/IncidentResponsePlan').then(m => ({ default: m.IncidentResponsePlan })));
const ContingencyPlan = lazy(() => import('../client/compliance/ContingencyPlan').then(m => ({ default: m.ContingencyPlan })));
const WorkforceAccess = lazy(() => import('../client/compliance/WorkforceAccess').then(m => ({ default: m.WorkforceAccess })));
const PhysicalSafeguards = lazy(() => import('../client/compliance/PhysicalSafeguards').then(m => ({ default: m.PhysicalSafeguards })));
const OfficerDesignation = lazy(() => import('../client/compliance/OfficerDesignation').then(m => ({ default: m.OfficerDesignation })));
const GapWizard = lazy(() => import('../client/compliance/GapWizard').then(m => ({ default: m.GapWizard })));

const moduleComponents: Record<string, React.LazyExoticComponent<React.FC<{ apiBase?: string }>>> = {
  sra: SRAWizard,
  policies: PolicyLibrary,
  training: TrainingTracker,
  baas: BAATracker,
  'ir-plan': IncidentResponsePlan,
  contingency: ContingencyPlan,
  workforce: WorkforceAccess,
  physical: PhysicalSafeguards,
  officers: OfficerDesignation,
  'gap-analysis': GapWizard,
};

export const CompanionModuleWork: React.FC = () => {
  const { orgId, moduleKey } = useParams<{ orgId: string; moduleKey: string }>();
  const [notesOpen, setNotesOpen] = useState(false);
  const [alertFormOpen, setAlertFormOpen] = useState(false);
  const [alertStatus, setAlertStatus] = useState('complete');
  const [alertDate, setAlertDate] = useState('');
  const [alertDesc, setAlertDesc] = useState('');

  const { data: alertsData } = useCompanionAlerts(orgId, moduleKey);
  const createAlert = useCreateAlert(orgId || '');
  const updateAlert = useUpdateAlert();
  const deleteAlert = useDeleteAlert();

  const moduleDef = MODULE_DEFS.find(m => m.key === moduleKey);
  const Component = moduleKey ? moduleComponents[moduleKey] : null;

  if (!orgId || !moduleKey || !Component || !moduleDef) {
    return (
      <div className="text-center py-20" style={{ color: companionColors.textTertiary }}>
        Module not found.
      </div>
    );
  }

  const moduleAlerts = (alertsData?.alerts || []).filter((a: ModuleAlert) => a.status !== 'dismissed' && a.status !== 'resolved');
  const apiBase = `/api/companion/clients/${orgId}`;

  return (
    <div>
      {/* Module header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-semibold" style={{ color: companionColors.textPrimary }}>
            {moduleDef.label}
          </h2>
          <p className="text-sm mt-0.5" style={{ color: companionColors.textSecondary }}>
            Working on behalf of the client
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setAlertFormOpen(!alertFormOpen)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-90"
            style={{
              background: moduleAlerts.length > 0 ? companionColors.actionNeededLight : companionColors.primaryLight,
              color: moduleAlerts.length > 0 ? companionColors.actionNeeded : companionColors.primary,
              border: `1px solid ${moduleAlerts.length > 0 ? companionColors.actionNeeded : companionColors.primary}40`,
            }}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M8 2a4 4 0 0 1 4 4c0 3 1 4 1 4H3s1-1 1-4a4 4 0 0 1 4-4" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M6.5 12a1.5 1.5 0 0 0 3 0" strokeLinecap="round" />
            </svg>
            {moduleAlerts.length > 0 ? `Alert (${moduleAlerts.length})` : 'Set Alert'}
          </button>
          <button
            onClick={() => setNotesOpen(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-90"
            style={{
              background: companionColors.amberLight,
              color: companionColors.amberDark,
              border: `1px solid ${companionColors.amber}40`,
            }}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 3h10v10H3z" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M5 6h6M5 8h6M5 10h3" strokeLinecap="round" />
            </svg>
            Notes
          </button>
        </div>
      </div>

      {/* Alert form / existing alerts */}
      {alertFormOpen && (
        <div
          className="rounded-xl p-4 mb-4"
          style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
        >
          <h4 className="text-sm font-semibold mb-3" style={{ color: companionColors.textPrimary }}>
            {moduleAlerts.length > 0 ? 'Module Alerts' : 'Set Compliance Alert'}
          </h4>

          {/* Existing alerts */}
          {moduleAlerts.map((a: ModuleAlert) => (
            <div
              key={a.id}
              className="flex items-center justify-between px-3 py-2 rounded-lg mb-2"
              style={{
                background: a.status === 'triggered' ? companionColors.actionNeededLight : companionColors.amberLight,
                border: `1px solid ${a.status === 'triggered' ? '#FECACA' : '#FDE68A'}`,
              }}
            >
              <div>
                <span className="text-sm" style={{ color: companionColors.textPrimary }}>
                  Expected "{a.expected_status.replace('_', ' ')}" by {new Date(a.target_date).toLocaleDateString()}
                </span>
                {a.description && (
                  <p className="text-xs" style={{ color: companionColors.textTertiary }}>{a.description}</p>
                )}
              </div>
              <div className="flex gap-1">
                <button
                  onClick={() => updateAlert.mutate({ alertId: a.id, status: 'dismissed' })}
                  className="text-xs px-2 py-1 rounded hover:opacity-80"
                  style={{ color: companionColors.textTertiary }}
                >
                  Dismiss
                </button>
                <button
                  onClick={() => deleteAlert.mutate(a.id)}
                  className="text-xs px-2 py-1 rounded hover:opacity-80"
                  style={{ color: companionColors.actionNeeded }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}

          {/* New alert form */}
          <div className="flex items-end gap-3 mt-2">
            <div>
              <label className="block text-xs mb-1" style={{ color: companionColors.textSecondary }}>Expected Status</label>
              <select
                value={alertStatus}
                onChange={e => setAlertStatus(e.target.value)}
                className="px-3 py-1.5 rounded-lg text-sm"
                style={{ border: `1px solid ${companionColors.cardBorder}`, color: companionColors.textPrimary }}
              >
                <option value="complete">Complete</option>
                <option value="in_progress">In Progress</option>
              </select>
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: companionColors.textSecondary }}>Target Date</label>
              <input
                type="date"
                value={alertDate}
                onChange={e => setAlertDate(e.target.value)}
                className="px-3 py-1.5 rounded-lg text-sm"
                style={{ border: `1px solid ${companionColors.cardBorder}`, color: companionColors.textPrimary }}
              />
            </div>
            <div className="flex-1">
              <label className="block text-xs mb-1" style={{ color: companionColors.textSecondary }}>Note (optional)</label>
              <input
                value={alertDesc}
                onChange={e => setAlertDesc(e.target.value)}
                placeholder="e.g. Must complete before audit"
                className="w-full px-3 py-1.5 rounded-lg text-sm"
                style={{ border: `1px solid ${companionColors.cardBorder}`, color: companionColors.textPrimary }}
              />
            </div>
            <button
              onClick={() => {
                if (!alertDate || !moduleKey) return;
                createAlert.mutate({
                  module_key: moduleKey,
                  expected_status: alertStatus,
                  target_date: alertDate,
                  description: alertDesc || undefined,
                }, {
                  onSuccess: () => { setAlertDate(''); setAlertDesc(''); },
                });
              }}
              disabled={!alertDate || createAlert.isPending}
              className="px-4 py-1.5 rounded-lg text-sm font-medium text-white transition-colors disabled:opacity-50"
              style={{ background: companionColors.primary }}
            >
              {createAlert.isPending ? 'Saving...' : 'Add Alert'}
            </button>
          </div>
        </div>
      )}

      {/* Compliance form */}
      <div
        className="rounded-xl p-6"
        style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
      >
        <Suspense fallback={<div className="flex justify-center py-12"><Spinner size="lg" /></div>}>
          <Component apiBase={apiBase} />
        </Suspense>
      </div>

      {/* Notes drawer */}
      <CompanionNotes orgId={orgId} moduleKey={moduleKey} isOpen={notesOpen} onClose={() => setNotesOpen(false)} />
    </div>
  );
};

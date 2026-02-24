import React, { useState, lazy, Suspense } from 'react';
import { useParams } from 'react-router-dom';
import { companionColors, MODULE_DEFS } from './companion-tokens';
import { CompanionNotes } from './CompanionNotes';
import { Spinner } from '../components/shared';

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

  const moduleDef = MODULE_DEFS.find(m => m.key === moduleKey);
  const Component = moduleKey ? moduleComponents[moduleKey] : null;

  if (!orgId || !moduleKey || !Component || !moduleDef) {
    return (
      <div className="text-center py-20" style={{ color: companionColors.textTertiary }}>
        Module not found.
      </div>
    );
  }

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

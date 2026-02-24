import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useCompanionClientOverview, useCompanionNotes, useClientActivity } from './useCompanionApi';
import { companionColors, MODULE_DEFS } from './companion-tokens';
import { Spinner } from '../components/shared';

function getModuleStatusFromOverview(overview: any): Record<string, { status: string; detail: string }> {
  if (!overview) return {};
  const m: Record<string, { status: string; detail: string }> = {};

  m.sra = overview.sra?.status === 'completed'
    ? { status: 'complete', detail: `Risk score: ${overview.sra.risk_score ?? 'N/A'}` }
    : overview.sra?.status === 'in_progress'
    ? { status: 'in_progress', detail: 'Assessment in progress' }
    : { status: 'not_started', detail: 'Not started' };

  const pActive = overview.policies?.active || 0;
  const pTotal = overview.policies?.total || 0;
  const pReview = overview.policies?.review_due || 0;
  m.policies = pReview > 0
    ? { status: 'action_needed', detail: `${pReview} policies need review` }
    : pActive > 0
    ? { status: 'complete', detail: `${pActive} active policies` }
    : pTotal > 0
    ? { status: 'in_progress', detail: `${pTotal} draft policies` }
    : { status: 'not_started', detail: 'No policies created' };

  const tOverdue = overview.training?.overdue || 0;
  const tCompliant = overview.training?.compliant || 0;
  const tTotal = overview.training?.total_employees || 0;
  m.training = tOverdue > 0
    ? { status: 'action_needed', detail: `${tOverdue} overdue` }
    : tCompliant > 0
    ? { status: 'complete', detail: `${tCompliant}/${tTotal} trained` }
    : tTotal > 0
    ? { status: 'in_progress', detail: `${tTotal} records` }
    : { status: 'not_started', detail: 'No training records' };

  const bExpiring = overview.baas?.expiring_soon || 0;
  const bActive = overview.baas?.active || 0;
  m.baas = bExpiring > 0
    ? { status: 'action_needed', detail: `${bExpiring} expiring soon` }
    : bActive > 0
    ? { status: 'complete', detail: `${bActive} active BAAs` }
    : { status: 'not_started', detail: 'No BAAs' };

  m['ir-plan'] = overview.ir_plan?.status === 'active'
    ? { status: 'complete', detail: `${overview.ir_plan.breaches || 0} breach records` }
    : overview.ir_plan?.status !== 'not_started'
    ? { status: 'in_progress', detail: 'Plan created' }
    : { status: 'not_started', detail: 'No IR plan' };

  const cPlans = overview.contingency?.plans || 0;
  m.contingency = cPlans > 0
    ? overview.contingency?.all_tested
      ? { status: 'complete', detail: `${cPlans} plans, all tested` }
      : { status: 'in_progress', detail: `${cPlans} plans, not all tested` }
    : { status: 'not_started', detail: 'No contingency plans' };

  const wPending = overview.workforce?.pending_termination || 0;
  const wActive = overview.workforce?.active || 0;
  m.workforce = wPending > 0
    ? { status: 'action_needed', detail: `${wPending} pending access revocation` }
    : wActive > 0
    ? { status: 'complete', detail: `${wActive} active members` }
    : { status: 'not_started', detail: 'No workforce records' };

  const phGaps = overview.physical?.gaps || 0;
  const phAssessed = overview.physical?.assessed || 0;
  m.physical = phGaps > 0
    ? { status: 'action_needed', detail: `${phGaps} gaps found` }
    : phAssessed > 0
    ? { status: 'complete', detail: `${phAssessed} items assessed` }
    : { status: 'not_started', detail: 'Not assessed' };

  const hasPrivacy = !!overview.officers?.privacy_officer;
  const hasSecurity = !!overview.officers?.security_officer;
  m.officers = hasPrivacy && hasSecurity
    ? { status: 'complete', detail: `${overview.officers.privacy_officer} / ${overview.officers.security_officer}` }
    : hasPrivacy || hasSecurity
    ? { status: 'in_progress', detail: 'One officer designated' }
    : { status: 'not_started', detail: 'No officers designated' };

  const gapPct = overview.gap_analysis?.completion || 0;
  m['gap-analysis'] = gapPct >= 90
    ? { status: 'complete', detail: `${gapPct}% complete, maturity ${overview.gap_analysis?.maturity_avg || 0}` }
    : gapPct > 0
    ? { status: 'in_progress', detail: `${gapPct}% complete` }
    : { status: 'not_started', detail: 'Not started' };

  return m;
}

const statusStyles: Record<string, { bg: string; color: string; label: string }> = {
  complete: { bg: companionColors.completeLight, color: companionColors.complete, label: 'Complete' },
  in_progress: { bg: companionColors.inProgressLight, color: companionColors.inProgress, label: 'In Progress' },
  not_started: { bg: companionColors.notStartedLight, color: companionColors.notStarted, label: 'Not Started' },
  action_needed: { bg: companionColors.actionNeededLight, color: companionColors.actionNeeded, label: 'Action Needed' },
};

export const CompanionClientDetail: React.FC = () => {
  const { orgId } = useParams<{ orgId: string }>();
  const navigate = useNavigate();
  const { data: overviewData, isLoading } = useCompanionClientOverview(orgId);
  const { data: notesData } = useCompanionNotes(orgId);
  const { data: activityData } = useClientActivity(orgId, 10);

  if (isLoading || !overviewData) {
    return <div className="flex justify-center py-20"><Spinner size="lg" /></div>;
  }

  const moduleStatus = getModuleStatusFromOverview(overviewData);
  const readiness = overviewData.overall_readiness || 0;
  const notes = notesData?.notes || [];
  const activity = activityData?.activity || [];

  return (
    <div className="flex gap-6">
      {/* Left — Journey Stepper */}
      <div className="w-64 flex-shrink-0">
        <div
          className="rounded-xl p-4 sticky top-24"
          style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
        >
          <h3 className="font-semibold text-sm mb-4" style={{ color: companionColors.textPrimary }}>
            HIPAA Journey
          </h3>
          <div className="space-y-1">
            {MODULE_DEFS.map((mod, i) => {
              const ms = moduleStatus[mod.key] || { status: 'not_started', detail: '' };
              const st = statusStyles[ms.status] || statusStyles.not_started;
              return (
                <button
                  key={mod.key}
                  onClick={() => navigate(`/companion/clients/${orgId}/${mod.key}`)}
                  className="w-full text-left flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors hover:opacity-80"
                  style={{ background: 'transparent' }}
                >
                  {/* Step number */}
                  <div
                    className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
                    style={{ background: st.bg, color: st.color }}
                  >
                    {ms.status === 'complete' ? '\u2713' : i + 1}
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate" style={{ color: companionColors.textPrimary }}>
                      {mod.shortLabel}
                    </p>
                    <p className="text-xs truncate" style={{ color: st.color }}>
                      {st.label}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Right — Overview + Activity */}
      <div className="flex-1 min-w-0">
        {/* Readiness card */}
        <div
          className="rounded-xl p-6 mb-6"
          style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-xl font-semibold" style={{ color: companionColors.textPrimary }}>
                {overviewData.org_name}
              </h2>
              <p className="text-sm mt-0.5" style={{ color: companionColors.textSecondary }}>
                Overall HIPAA Readiness
              </p>
            </div>
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center text-lg font-bold"
              style={{
                background: readiness >= 70 ? companionColors.completeLight
                  : readiness >= 40 ? companionColors.amberLight
                  : companionColors.notStartedLight,
                color: readiness >= 70 ? companionColors.complete
                  : readiness >= 40 ? companionColors.amber
                  : companionColors.notStarted,
              }}
            >
              {Math.round(readiness)}%
            </div>
          </div>

          {/* Progress bar */}
          <div className="w-full h-3 rounded-full" style={{ background: companionColors.notStartedLight }}>
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.min(readiness, 100)}%`,
                background: readiness >= 70 ? companionColors.complete
                  : readiness >= 40 ? companionColors.amber
                  : companionColors.inProgress,
              }}
            />
          </div>
        </div>

        {/* Module grid */}
        <div className="grid grid-cols-2 gap-3 mb-6">
          {MODULE_DEFS.map(mod => {
            const ms = moduleStatus[mod.key] || { status: 'not_started', detail: '' };
            const st = statusStyles[ms.status] || statusStyles.not_started;
            return (
              <button
                key={mod.key}
                onClick={() => navigate(`/companion/clients/${orgId}/${mod.key}`)}
                className="text-left rounded-xl p-4 transition-all hover:shadow-md"
                style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <h4 className="text-sm font-semibold" style={{ color: companionColors.textPrimary }}>
                    {mod.label}
                  </h4>
                  <span
                    className="text-xs px-2 py-0.5 rounded-full font-medium"
                    style={{ background: st.bg, color: st.color }}
                  >
                    {st.label}
                  </span>
                </div>
                <p className="text-xs" style={{ color: companionColors.textSecondary }}>
                  {ms.detail}
                </p>
              </button>
            );
          })}
        </div>

        {/* Recent notes & activity */}
        <div className="grid grid-cols-2 gap-4">
          <div
            className="rounded-xl p-4"
            style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
          >
            <h4 className="text-sm font-semibold mb-3" style={{ color: companionColors.textPrimary }}>
              Recent Notes ({notes.length})
            </h4>
            {notes.length === 0 ? (
              <p className="text-xs" style={{ color: companionColors.textTertiary }}>No notes yet.</p>
            ) : (
              <div className="space-y-2">
                {notes.slice(0, 5).map((n: any) => (
                  <div key={n.id} className="text-xs" style={{ color: companionColors.textSecondary }}>
                    <span className="font-medium capitalize">{n.module_key}</span>: {n.note.slice(0, 80)}{n.note.length > 80 ? '...' : ''}
                    <br />
                    <span style={{ color: companionColors.textTertiary }}>
                      {new Date(n.created_at).toLocaleDateString()}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div
            className="rounded-xl p-4"
            style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
          >
            <h4 className="text-sm font-semibold mb-3" style={{ color: companionColors.textPrimary }}>
              Recent Activity
            </h4>
            {activity.length === 0 ? (
              <p className="text-xs" style={{ color: companionColors.textTertiary }}>No activity yet.</p>
            ) : (
              <div className="space-y-2">
                {activity.slice(0, 5).map((a: any) => (
                  <div key={a.id} className="text-xs" style={{ color: companionColors.textSecondary }}>
                    <span className="font-medium">{a.action.replace(/_/g, ' ')}</span>
                    {a.module_key && <span className="capitalize"> ({a.module_key})</span>}
                    <br />
                    <span style={{ color: companionColors.textTertiary }}>
                      {a.companion_name} &middot; {new Date(a.created_at).toLocaleDateString()}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

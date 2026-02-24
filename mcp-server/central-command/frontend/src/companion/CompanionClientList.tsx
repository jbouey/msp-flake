import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCompanionClients } from './useCompanionApi';
import { companionColors, MODULE_DEFS } from './companion-tokens';
import { Spinner } from '../components/shared';

function getModuleStatus(overview: any): Record<string, 'complete' | 'in_progress' | 'not_started' | 'action_needed'> {
  if (!overview) return {};
  const m: Record<string, 'complete' | 'in_progress' | 'not_started' | 'action_needed'> = {};
  // SRA
  m.sra = overview.sra?.status === 'completed' ? 'complete'
    : overview.sra?.status === 'in_progress' ? 'in_progress' : 'not_started';
  // Policies
  m.policies = (overview.policies?.active || 0) > 0 ? 'complete'
    : (overview.policies?.total || 0) > 0 ? 'in_progress' : 'not_started';
  if ((overview.policies?.review_due || 0) > 0) m.policies = 'action_needed';
  // Training
  m.training = (overview.training?.overdue || 0) > 0 ? 'action_needed'
    : (overview.training?.compliant || 0) > 0 ? 'complete'
    : (overview.training?.total_employees || 0) > 0 ? 'in_progress' : 'not_started';
  // BAAs
  m.baas = (overview.baas?.expiring_soon || 0) > 0 ? 'action_needed'
    : (overview.baas?.active || 0) > 0 ? 'complete'
    : (overview.baas?.total || 0) > 0 ? 'in_progress' : 'not_started';
  // IR Plan
  m['ir-plan'] = overview.ir_plan?.status === 'active' ? 'complete'
    : overview.ir_plan?.status !== 'not_started' ? 'in_progress' : 'not_started';
  // Contingency
  m.contingency = (overview.contingency?.plans || 0) > 0
    ? (overview.contingency?.all_tested ? 'complete' : 'in_progress') : 'not_started';
  // Workforce
  m.workforce = (overview.workforce?.pending_termination || 0) > 0 ? 'action_needed'
    : (overview.workforce?.active || 0) > 0 ? 'complete' : 'not_started';
  // Physical
  m.physical = (overview.physical?.gaps || 0) > 0 ? 'action_needed'
    : (overview.physical?.assessed || 0) > 0 ? 'complete' : 'not_started';
  // Officers
  m.officers = overview.officers?.privacy_officer && overview.officers?.security_officer ? 'complete'
    : overview.officers?.privacy_officer || overview.officers?.security_officer ? 'in_progress' : 'not_started';
  // Gap
  m['gap-analysis'] = (overview.gap_analysis?.completion || 0) >= 90 ? 'complete'
    : (overview.gap_analysis?.completion || 0) > 0 ? 'in_progress' : 'not_started';
  return m;
}

const statusColor: Record<string, string> = {
  complete: companionColors.complete,
  in_progress: companionColors.inProgress,
  not_started: companionColors.notStarted,
  action_needed: companionColors.actionNeeded,
};

export const CompanionClientList: React.FC = () => {
  const { data, isLoading } = useCompanionClients();
  const [search, setSearch] = useState('');
  const navigate = useNavigate();

  const clients = (data?.clients || []).filter((c: any) =>
    !search || c.name.toLowerCase().includes(search.toLowerCase())
  );

  if (isLoading) {
    return <div className="flex justify-center py-20"><Spinner size="lg" /></div>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold" style={{ color: companionColors.textPrimary }}>
            Client Organizations
          </h1>
          <p className="text-sm mt-1" style={{ color: companionColors.textSecondary }}>
            {clients.length} active client{clients.length !== 1 ? 's' : ''}
          </p>
        </div>
        <input
          type="text"
          placeholder="Search clients..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="px-4 py-2 rounded-lg text-sm w-64 focus:outline-none focus:ring-2"
          style={{
            border: `1px solid ${companionColors.cardBorder}`,
            background: companionColors.cardBg,
            color: companionColors.textPrimary,
            // @ts-ignore
            '--tw-ring-color': companionColors.focusRing,
          }}
        />
      </div>

      {clients.length === 0 ? (
        <div className="text-center py-20" style={{ color: companionColors.textTertiary }}>
          {search ? 'No clients match your search.' : 'No active client organizations.'}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {clients.map((client: any) => {
            const moduleStatus = getModuleStatus(client.overview);
            const readiness = client.overview?.overall_readiness || 0;
            return (
              <button
                key={client.id}
                onClick={() => navigate(`/companion/clients/${client.id}`)}
                className="text-left rounded-xl p-5 transition-all hover:shadow-md"
                style={{
                  background: companionColors.cardBg,
                  border: `1px solid ${companionColors.cardBorder}`,
                  boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
                }}
              >
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <h3 className="font-semibold text-base" style={{ color: companionColors.textPrimary }}>
                      {client.name}
                    </h3>
                    <p className="text-xs mt-0.5" style={{ color: companionColors.textTertiary }}>
                      {client.practice_type || 'Healthcare Practice'}
                      {client.provider_count ? ` \u00b7 ${client.provider_count} provider${client.provider_count > 1 ? 's' : ''}` : ''}
                    </p>
                  </div>
                  <div
                    className="flex items-center justify-center w-12 h-12 rounded-full text-sm font-bold"
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

                {/* Module progress bar */}
                <div className="flex gap-0.5 mt-3">
                  {MODULE_DEFS.map(mod => (
                    <div
                      key={mod.key}
                      className="h-2 flex-1 rounded-full"
                      title={`${mod.label}: ${(moduleStatus[mod.key] || 'not_started').replace('_', ' ')}`}
                      style={{
                        background: statusColor[moduleStatus[mod.key] || 'not_started'] + '40',
                        position: 'relative',
                      }}
                    >
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: moduleStatus[mod.key] === 'complete' ? '100%'
                            : moduleStatus[mod.key] === 'in_progress' ? '50%'
                            : moduleStatus[mod.key] === 'action_needed' ? '75%' : '0%',
                          background: statusColor[moduleStatus[mod.key] || 'not_started'],
                        }}
                      />
                    </div>
                  ))}
                </div>
                <div className="flex gap-3 mt-2 text-xs" style={{ color: companionColors.textTertiary }}>
                  <span>{Object.values(moduleStatus).filter(s => s === 'complete').length}/10 complete</span>
                  {Object.values(moduleStatus).filter(s => s === 'action_needed').length > 0 && (
                    <span style={{ color: companionColors.actionNeeded }}>
                      {Object.values(moduleStatus).filter(s => s === 'action_needed').length} need attention
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

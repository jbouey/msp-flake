import React, { useState } from 'react';
import { GlassCard } from '../shared';
import { useIncidentBreakdown } from '../../hooks';
import { colors } from '../../tokens/style-tokens';

type Window = '24h' | '7d' | '30d';

function formatTypeName(raw: string): string {
  // Convert snake_case/kebab incident types to readable labels
  return raw
    .replace(/_/g, ' ')
    .replace(/-/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/Ssh/g, 'SSH')
    .replace(/Dns/g, 'DNS')
    .replace(/Smb/g, 'SMB')
    .replace(/Rdp/g, 'RDP')
    .replace(/Gpo/g, 'GPO')
    .replace(/Av /g, 'AV ')
    .replace(/Hipaa/g, 'HIPAA');
}

export const TopIncidentTypes: React.FC<{
  siteId?: string;
  className?: string;
}> = ({ siteId, className = '' }) => {
  const [window, setWindow] = useState<Window>('24h');
  const { data, isLoading } = useIncidentBreakdown(window, siteId);

  const topTypes = data?.top_types ?? [];
  const maxCount = topTypes.length > 0 ? topTypes[0].count : 0;

  return (
    <GlassCard className={className}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-base font-semibold text-label-primary">Top Incident Types</h3>
        <div className="flex items-center bg-fill-secondary rounded-ios-md p-0.5">
          {(['24h', '7d', '30d'] as Window[]).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={`px-2 py-0.5 text-[10px] font-medium rounded-md transition-all ${
                window === w
                  ? 'bg-white text-label-primary shadow-sm'
                  : 'text-label-tertiary hover:text-label-secondary'
              }`}
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="h-48 flex items-center justify-center">
          <div className="w-5 h-5 border-2 border-accent-primary/30 border-t-accent-primary rounded-full animate-spin" />
        </div>
      ) : topTypes.length === 0 ? (
        <div className="h-48 flex items-center justify-center text-sm text-label-tertiary">
          No incidents in this period
        </div>
      ) : (
        <div className="space-y-1.5">
          {topTypes.slice(0, 6).map((t) => {
            return (
              <div key={t.incident_type} className="group">
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-[11px] text-label-secondary font-medium truncate max-w-[180px]">
                    {formatTypeName(t.incident_type)}
                  </span>
                  <div className="flex items-center gap-1.5">
                    {t.l1 > 0 && <span className="text-[9px] font-semibold tabular-nums px-1 py-0 rounded" style={{ color: colors.levels.l1, backgroundColor: `${colors.levels.l1}12` }}>{t.l1}</span>}
                    {t.l2 > 0 && <span className="text-[9px] font-semibold tabular-nums px-1 py-0 rounded" style={{ color: colors.levels.l2, backgroundColor: `${colors.levels.l2}12` }}>{t.l2}</span>}
                    {t.l3 > 0 && <span className="text-[9px] font-semibold tabular-nums px-1 py-0 rounded" style={{ color: colors.levels.l3, backgroundColor: `${colors.levels.l3}12` }}>{t.l3}</span>}
                    <span className="text-[11px] font-bold tabular-nums text-label-primary ml-0.5">{t.count}</span>
                  </div>
                </div>
                <div className="h-1.5 bg-fill-secondary rounded-full overflow-hidden">
                  <div className="h-full flex">
                    {t.l1 > 0 && (
                      <div
                        className="h-full transition-all duration-300"
                        style={{
                          width: `${(t.l1 / maxCount) * 100}%`,
                          backgroundColor: colors.levels.l1,
                          opacity: 0.8,
                        }}
                      />
                    )}
                    {t.l2 > 0 && (
                      <div
                        className="h-full transition-all duration-300"
                        style={{
                          width: `${(t.l2 / maxCount) * 100}%`,
                          backgroundColor: colors.levels.l2,
                          opacity: 0.8,
                        }}
                      />
                    )}
                    {t.l3 > 0 && (
                      <div
                        className="h-full transition-all duration-300"
                        style={{
                          width: `${(t.l3 / maxCount) * 100}%`,
                          backgroundColor: colors.levels.l3,
                          opacity: 0.8,
                        }}
                      />
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </GlassCard>
  );
};

export default TopIncidentTypes;

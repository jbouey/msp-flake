import React, { useState } from 'react';
import { useCompanionActivity } from './useCompanionApi';
import { companionColors } from './companion-tokens';
import { Spinner } from '../components/shared';

export const CompanionActivityLog: React.FC = () => {
  const [limit, setLimit] = useState(100);
  const { data, isLoading } = useCompanionActivity(undefined, limit);

  const activity = data?.activity || [];

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold" style={{ color: companionColors.textPrimary }}>
            Activity Log
          </h1>
          <p className="text-sm mt-1" style={{ color: companionColors.textSecondary }}>
            All companion actions across clients
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-20"><Spinner size="lg" /></div>
      ) : activity.length === 0 ? (
        <div className="text-center py-20" style={{ color: companionColors.textTertiary }}>
          No activity recorded yet.
        </div>
      ) : (
        <div
          className="rounded-xl overflow-hidden"
          style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
        >
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: companionColors.sidebarBg, borderBottom: `1px solid ${companionColors.divider}` }}>
                <th className="text-left px-5 py-3 font-medium" style={{ color: companionColors.textSecondary }}>When</th>
                <th className="text-left px-5 py-3 font-medium" style={{ color: companionColors.textSecondary }}>Companion</th>
                <th className="text-left px-5 py-3 font-medium" style={{ color: companionColors.textSecondary }}>Client</th>
                <th className="text-left px-5 py-3 font-medium" style={{ color: companionColors.textSecondary }}>Action</th>
                <th className="text-left px-5 py-3 font-medium" style={{ color: companionColors.textSecondary }}>Module</th>
              </tr>
            </thead>
            <tbody>
              {activity.map((a: any) => (
                <tr
                  key={a.id}
                  style={{ borderBottom: `1px solid ${companionColors.divider}` }}
                >
                  <td className="px-5 py-3" style={{ color: companionColors.textSecondary }}>
                    {new Date(a.created_at).toLocaleString(undefined, {
                      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                    })}
                  </td>
                  <td className="px-5 py-3" style={{ color: companionColors.textPrimary }}>
                    {a.companion_name}
                  </td>
                  <td className="px-5 py-3" style={{ color: companionColors.textPrimary }}>
                    {a.org_name || '-'}
                  </td>
                  <td className="px-5 py-3" style={{ color: companionColors.textPrimary }}>
                    {a.action.replace(/_/g, ' ')}
                  </td>
                  <td className="px-5 py-3 capitalize" style={{ color: companionColors.textSecondary }}>
                    {a.module_key?.replace(/-/g, ' ') || '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {activity.length >= limit && (
            <div className="px-5 py-3 text-center" style={{ borderTop: `1px solid ${companionColors.divider}` }}>
              <button
                onClick={() => setLimit(l => l + 100)}
                className="text-sm font-medium hover:underline"
                style={{ color: companionColors.primary }}
              >
                Load more
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

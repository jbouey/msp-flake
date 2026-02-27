import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard, Spinner } from '../shared';
import { useAttentionRequired } from '../../hooks';
import type { AttentionItem } from '../../types';

function formatTimeAgo(timestamp: string | null): string {
  if (!timestamp) return '';
  const now = Date.now();
  const then = new Date(timestamp).getTime();
  const diffMin = Math.floor((now - then) / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  return `${Math.floor(diffHrs / 24)}d ago`;
}

const typeIcons: Record<string, React.ReactNode> = {
  l3_escalation: (
    <div className="w-8 h-8 rounded-full bg-health-critical/12 flex items-center justify-center flex-shrink-0">
      <svg className="w-4 h-4 text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    </div>
  ),
  repeat_failure: (
    <div className="w-8 h-8 rounded-full bg-health-warning/12 flex items-center justify-center flex-shrink-0">
      <svg className="w-4 h-4 text-health-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
      </svg>
    </div>
  ),
  offline_appliance: (
    <div className="w-8 h-8 rounded-full bg-label-tertiary/12 flex items-center justify-center flex-shrink-0">
      <svg className="w-4 h-4 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M18.364 5.636a9 9 0 010 12.728M5.636 5.636a9 9 0 000 12.728M12 12h.01" />
      </svg>
    </div>
  ),
};

const AttentionItemRow: React.FC<{
  item: AttentionItem;
  onClick: () => void;
}> = ({ item, onClick }) => (
  <button
    onClick={onClick}
    className="w-full flex items-start gap-3 p-3 rounded-ios-md text-left transition-all hover:bg-fill-secondary"
  >
    {typeIcons[item.type] || typeIcons.offline_appliance}
    <div className="flex-1 min-w-0">
      <p className="text-sm font-medium text-label-primary leading-snug truncate">
        {item.title}
      </p>
      <p className="text-xs text-label-tertiary mt-0.5 leading-relaxed">
        {item.clinic_name && <span className="font-medium text-label-secondary">{item.clinic_name}</span>}
        {item.clinic_name && item.detail && <span> — </span>}
        {item.detail}
      </p>
    </div>
    <span className="text-[10px] text-label-tertiary whitespace-nowrap mt-0.5 tabular-nums">
      {formatTimeAgo(item.timestamp)}
    </span>
  </button>
);

export const AttentionPanel: React.FC<{ className?: string }> = ({ className = '' }) => {
  const navigate = useNavigate();
  const { data, isLoading } = useAttentionRequired();

  const items = data?.items ?? [];
  const count = data?.count ?? 0;

  const handleItemClick = (item: AttentionItem) => {
    if (item.site_id) {
      navigate(`/sites/${item.site_id}`);
    }
  };

  return (
    <GlassCard className={className}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <h3 className="text-base font-semibold text-label-primary">Needs Attention</h3>
          {count > 0 && (
            <span className="px-2 py-0.5 text-xs font-bold bg-health-critical text-white rounded-full leading-snug">
              {count}
            </span>
          )}
        </div>
        {data && count > 0 && (
          <div className="flex items-center gap-3 text-[10px] text-label-tertiary">
            {data.l3_count > 0 && (
              <span className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-health-critical" />
                {data.l3_count} L3
              </span>
            )}
            {data.repeat_count > 0 && (
              <span className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-health-warning" />
                {data.repeat_count} repeat
              </span>
            )}
            {data.offline_count > 0 && (
              <span className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-label-tertiary" />
                {data.offline_count} offline
              </span>
            )}
          </div>
        )}
      </div>

      <div className="max-h-[300px] overflow-y-auto -mx-1 px-1">
        {isLoading ? (
          <div className="py-8 flex justify-center"><Spinner size="sm" /></div>
        ) : count === 0 ? (
          <div className="py-10 text-center">
            <div className="w-10 h-10 rounded-full bg-health-healthy/10 flex items-center justify-center mx-auto mb-3">
              <svg className="w-5 h-5 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <p className="text-sm font-medium text-label-primary">All clear</p>
            <p className="text-xs text-label-tertiary mt-0.5">No items require human attention</p>
          </div>
        ) : (
          <div className="space-y-0.5">
            {items.map((item, i) => (
              <AttentionItemRow
                key={`${item.type}-${item.site_id}-${i}`}
                item={item}
                onClick={() => handleItemClick(item)}
              />
            ))}
          </div>
        )}
      </div>
    </GlassCard>
  );
};

export default AttentionPanel;

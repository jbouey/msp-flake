import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard, Spinner } from '../shared';
import { useAttentionRequired } from '../../hooks';
import type { AttentionItem } from '../../types';
import { CHECK_TYPE_LABELS } from '../../types';
import { formatTimeAgo, cleanAttentionTitle } from '../../constants';

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
        {cleanAttentionTitle(item.title, CHECK_TYPE_LABELS)}
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

// Items older than this age count as "backlog" instead of "fresh attention".
// 24h is the threshold where an unresolved L3 escalation is no longer a
// surprise — it's an operational debt we're actively servicing.
const STALE_THRESHOLD_MS = 24 * 60 * 60 * 1000;

function isStale(timestamp: string | null): boolean {
  if (!timestamp) return false;
  const age = Date.now() - new Date(timestamp).getTime();
  return age > STALE_THRESHOLD_MS;
}

export const AttentionPanel: React.FC<{ className?: string }> = ({ className = '' }) => {
  const navigate = useNavigate();
  const { data, isLoading } = useAttentionRequired();
  // Backlog sub-list (items >24h old) is collapsed by default so the fresh
  // items stay above the fold. Operator can expand when they want to work
  // through the debt pile.
  const [showBacklog, setShowBacklog] = React.useState(false);

  const items = data?.items ?? [];
  const { freshItems, staleItems } = React.useMemo(() => {
    const fresh: AttentionItem[] = [];
    const stale: AttentionItem[] = [];
    for (const item of items) {
      if (isStale(item.timestamp)) {
        stale.push(item);
      } else {
        fresh.push(item);
      }
    }
    return { freshItems: fresh, staleItems: stale };
  }, [items]);

  const count = data?.count ?? 0;
  const freshCount = freshItems.length;

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
          {freshCount > 0 && (
            <span className="px-2 py-0.5 text-xs font-bold bg-health-critical text-white rounded-full leading-snug">
              {freshCount}
            </span>
          )}
          {staleItems.length > 0 && (
            <span
              className="px-2 py-0.5 text-[10px] font-medium bg-fill-secondary text-label-tertiary rounded-full leading-snug"
              title={`${staleItems.length} item(s) older than 24h — backlog`}
            >
              +{staleItems.length} backlog
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
          <>
            {/* Fresh items — things that happened in the last 24h */}
            {freshItems.length > 0 && (
              <div className="space-y-0.5">
                {freshItems.map((item, i) => (
                  <AttentionItemRow
                    key={`fresh-${item.type}-${item.site_id}-${i}`}
                    item={item}
                    onClick={() => handleItemClick(item)}
                  />
                ))}
              </div>
            )}

            {freshItems.length === 0 && staleItems.length > 0 && (
              <div className="py-6 text-center">
                <p className="text-sm text-label-secondary">No new items in the last 24h</p>
                <p className="text-xs text-label-tertiary mt-0.5">
                  {staleItems.length} older item(s) in backlog below
                </p>
              </div>
            )}

            {/* Collapsible backlog — items >24h old that haven't been resolved */}
            {staleItems.length > 0 && (
              <div className="mt-3 pt-3 border-t border-glass-border">
                <button
                  onClick={() => setShowBacklog((v) => !v)}
                  className="w-full flex items-center justify-between text-xs text-label-tertiary hover:text-label-secondary px-3 py-1.5 rounded-ios-sm hover:bg-fill-secondary transition-colors"
                >
                  <span className="uppercase tracking-wide font-medium">
                    Backlog ({staleItems.length}) — older than 24h
                  </span>
                  <svg
                    className={`w-3 h-3 transition-transform ${showBacklog ? 'rotate-180' : ''}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2.5}
                    aria-hidden
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                {showBacklog && (
                  <div className="space-y-0.5 mt-1 opacity-75">
                    {staleItems.map((item, i) => (
                      <AttentionItemRow
                        key={`stale-${item.type}-${item.site_id}-${i}`}
                        item={item}
                        onClick={() => handleItemClick(item)}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </GlassCard>
  );
};

export default AttentionPanel;

import React, { useState } from 'react';
import { GlassCard } from '../../../components/shared';
import type { OrderType } from '../../../utils/api';

export interface SiteActionToolbarProps {
  applianceCount: number;
  onBroadcast: (orderType: OrderType) => void;
  onClearStale: () => void;
  isLoading: boolean;
}

/**
 * Site action toolbar for bulk operations
 */
export const SiteActionToolbar: React.FC<SiteActionToolbarProps> = ({ applianceCount, onBroadcast, onClearStale, isLoading }) => {
  const [showClearConfirm, setShowClearConfirm] = useState(false);

  if (applianceCount === 0) return null;

  return (
    <>
      <div className="flex items-center gap-2 mb-4">
        <span className="text-label-tertiary text-sm mr-2">Site Actions:</span>
        <button
          onClick={() => onBroadcast('force_checkin')}
          disabled={isLoading}
          className="px-3 py-1.5 text-xs rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 disabled:opacity-50 transition-colors"
        >
          Force All Checkin
        </button>
        <button
          onClick={() => onBroadcast('sync_rules')}
          disabled={isLoading}
          className="px-3 py-1.5 text-xs rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary disabled:opacity-50 transition-colors"
        >
          Sync All Rules
        </button>
        <button
          onClick={() => setShowClearConfirm(true)}
          disabled={isLoading}
          className="px-3 py-1.5 text-xs rounded-ios bg-health-warning/10 text-health-warning hover:bg-health-warning/20 disabled:opacity-50 transition-colors"
        >
          Clear Stale
        </button>
      </div>

      {/* Clear Stale Confirmation Modal */}
      {showClearConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <GlassCard className="w-full max-w-sm">
            <h2 className="text-lg font-semibold text-label-primary mb-2">Clear Stale Appliances?</h2>
            <p className="text-label-secondary text-sm mb-4">
              This will remove all appliances that haven't checked in for more than 24 hours.
              This action cannot be undone.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setShowClearConfirm(false)}
                className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  onClearStale();
                  setShowClearConfirm(false);
                }}
                className="flex-1 px-4 py-2 rounded-ios bg-health-warning text-white"
              >
                Clear Stale
              </button>
            </div>
          </GlassCard>
        </div>
      )}
    </>
  );
};

import React, { useState } from 'react';
import { GlassCard, Spinner } from '../../../components/shared';

export interface MoveApplianceModalProps {
  applianceId: string;
  currentSiteId: string;
  onClose: () => void;
  onMove: (applianceId: string, targetSiteId: string) => void;
}

/**
 * Move Appliance Modal
 */
export const MoveApplianceModal: React.FC<MoveApplianceModalProps> = ({ applianceId, currentSiteId, onClose, onMove }) => {
  const [targetSiteId, setTargetSiteId] = useState('');
  const [sites, setSites] = useState<Array<{ site_id: string; clinic_name: string }>>([]);
  const [isLoading, setIsLoading] = useState(true);

  React.useEffect(() => {
    const fetchSites = async () => {
      try {
        const res = await fetch('/api/sites', {
          credentials: 'same-origin',
        });
        if (res.ok) {
          const data = await res.json();
          setSites((data.sites || []).filter((s: { site_id: string }) => s.site_id !== currentSiteId));
        }
      } catch {
        // ignore
      } finally {
        setIsLoading(false);
      }
    };
    fetchSites();
  }, [currentSiteId]);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="w-full max-w-md" onClick={e => e.stopPropagation()}>
      <GlassCard>
        <h2 className="text-xl font-semibold mb-4">Move Appliance</h2>
        <p className="text-sm text-label-secondary mb-4">
          Move <span className="font-mono text-xs">{applianceId.slice(0, 30)}...</span> to a different site.
        </p>
        {isLoading ? (
          <div className="flex justify-center py-8"><Spinner size="md" /></div>
        ) : sites.length === 0 ? (
          <p className="text-label-tertiary text-center py-8">No other sites available.</p>
        ) : (
          <div className="space-y-3">
            <select value={targetSiteId} onChange={e => setTargetSiteId(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
              <option value="">Select target site...</option>
              {sites.map(s => (
                <option key={s.site_id} value={s.site_id}>{s.clinic_name}</option>
              ))}
            </select>
            <div className="flex gap-3 pt-2">
              <button onClick={onClose}
                className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm">Cancel</button>
              <button onClick={() => onMove(applianceId, targetSiteId)} disabled={!targetSiteId}
                className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50 text-sm">
                Move Appliance
              </button>
            </div>
          </div>
        )}
      </GlassCard>
      </div>
    </div>
  );
};

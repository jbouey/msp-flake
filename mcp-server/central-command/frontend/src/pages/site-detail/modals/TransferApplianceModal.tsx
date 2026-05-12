import React, { useState } from 'react';
import { GlassCard, Spinner } from '../../../components/shared';
import type { SiteAppliance } from '../../../utils/api';
import { applianceApi } from '../../../utils/api';

export interface TransferApplianceModalProps {
  appliances: SiteAppliance[];
  currentSiteId: string;
  onClose: () => void;
  onTransferred: () => void;
  showToast: (msg: string, type: 'success' | 'error') => void;
}

/**
 * Transfer Appliance Modal — move an appliance to a different site by MAC address
 */
export const TransferApplianceModal: React.FC<TransferApplianceModalProps> = ({ appliances, currentSiteId, onClose, onTransferred, showToast }) => {
  const [selectedMac, setSelectedMac] = useState(appliances.length === 1 ? (appliances[0].mac_address || '') : '');
  const [targetSiteId, setTargetSiteId] = useState('');
  const [sites, setSites] = useState<Array<{ site_id: string; clinic_name: string }>>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isTransferring, setIsTransferring] = useState(false);

  React.useEffect(() => {
    const fetchSites = async () => {
      try {
        const res = await fetch('/api/sites', { credentials: 'include' });
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

  const handleTransfer = async () => {
    if (!selectedMac || !targetSiteId) return;
    setIsTransferring(true);
    try {
      const result = await applianceApi.transfer(selectedMac, currentSiteId, targetSiteId);
      showToast(`Appliance transferred to ${result.to_site_name}`, 'success');
      onTransferred();
    } catch (err) {
      showToast(`Transfer failed: ${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setIsTransferring(false);
    }
  };

  const appliancesWithMac = appliances.filter(a => a.mac_address);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="w-full max-w-md" onClick={e => e.stopPropagation()}>
        <GlassCard>
          <h2 className="text-xl font-semibold mb-4 text-label-primary">Transfer Appliance</h2>
          <p className="text-sm text-label-secondary mb-4">
            Move an appliance from this site to a different site. The appliance will pick up its new configuration on the next check-in.
          </p>
          {isLoading ? (
            <div className="flex justify-center py-8"><Spinner size="md" /></div>
          ) : appliancesWithMac.length === 0 ? (
            <p className="text-label-tertiary text-center py-8">No appliances with MAC addresses available to transfer.</p>
          ) : sites.length === 0 ? (
            <p className="text-label-tertiary text-center py-8">No other sites available.</p>
          ) : (
            <div className="space-y-3">
              {appliancesWithMac.length > 1 && (
                <div>
                  <label className="block text-sm font-medium text-label-secondary mb-1">Appliance</label>
                  <select value={selectedMac} onChange={e => setSelectedMac(e.target.value)}
                    className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                    <option value="">Select appliance...</option>
                    {appliancesWithMac.map(a => (
                      <option key={a.appliance_id} value={a.mac_address || ''}>
                        {a.hostname || 'Unknown'} ({a.mac_address})
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {appliancesWithMac.length === 1 && (
                <div className="text-sm text-label-secondary">
                  Appliance: <span className="font-mono">{appliancesWithMac[0].hostname || appliancesWithMac[0].mac_address}</span>
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-label-secondary mb-1">Destination Site</label>
                <select value={targetSiteId} onChange={e => setTargetSiteId(e.target.value)}
                  className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                  <option value="">Select target site...</option>
                  {sites.map(s => (
                    <option key={s.site_id} value={s.site_id}>{s.clinic_name}</option>
                  ))}
                </select>
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={onClose}
                  className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm">
                  Cancel
                </button>
                <button onClick={handleTransfer} disabled={!selectedMac || !targetSiteId || isTransferring}
                  className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50 text-sm">
                  {isTransferring ? 'Transferring...' : 'Transfer Appliance'}
                </button>
              </div>
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
};

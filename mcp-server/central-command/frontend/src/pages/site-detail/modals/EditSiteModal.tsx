import React, { useState } from 'react';
import { GlassCard } from '../../../components/shared';
import type { SiteDetail as SiteDetailType } from '../../../utils/api';
import { getCsrfTokenOrEmpty } from '../../../utils/csrf';

export interface EditSiteModalProps {
  site: SiteDetailType;
  onClose: () => void;
  onSaved: () => void;
  showToast: (msg: string, type: 'success' | 'error') => void;
}

/**
 * Edit Site Modal
 */
export const EditSiteModal: React.FC<EditSiteModalProps> = ({ site, onClose, onSaved, showToast }) => {
  const [clinicName, setClinicName] = useState(site.clinic_name || '');
  const [contactName, setContactName] = useState(site.contact_name || '');
  const [contactEmail, setContactEmail] = useState(site.contact_email || '');
  const [contactPhone, setContactPhone] = useState(site.contact_phone || '');
  const [tier, setTier] = useState(site.tier || 'mid');
  const [stage, setStage] = useState(site.onboarding_stage || 'active');
  const [isSaving, setIsSaving] = useState(false);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const res = await fetch(`/api/sites/${site.site_id}`, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfTokenOrEmpty() },
        body: JSON.stringify({
          clinic_name: clinicName,
          contact_name: contactName || null,
          contact_email: contactEmail || null,
          contact_phone: contactPhone || null,
          tier,
          onboarding_stage: stage,
        }),
      });
      if (res.ok) {
        showToast('Site updated', 'success');
        onSaved();
      } else {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        showToast(`Failed: ${err.detail}`, 'error');
      }
    } catch {
      showToast('Failed to update site', 'error');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="w-full max-w-lg" onClick={e => e.stopPropagation()}>
      <GlassCard>
        <h2 className="text-xl font-semibold mb-4">Edit Site</h2>
        <div className="space-y-4">
          <div>
            <label className="block text-xs text-label-tertiary uppercase mb-1">Clinic Name</label>
            <input type="text" value={clinicName} onChange={e => setClinicName(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Contact Name</label>
              <input type="text" value={contactName} onChange={e => setContactName(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
            </div>
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Contact Email</label>
              <input type="email" value={contactEmail} onChange={e => setContactEmail(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Phone</label>
              <input type="text" value={contactPhone} onChange={e => setContactPhone(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
            </div>
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Tier</label>
              <select value={tier} onChange={e => setTier(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                <option value="small">Small</option>
                <option value="mid">Mid</option>
                <option value="large">Large</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Stage</label>
              <select value={stage} onChange={e => setStage(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                <option value="provisioning">Provisioning</option>
                <option value="connectivity">Connectivity</option>
                <option value="scanning">Scanning</option>
                <option value="active">Active</option>
              </select>
            </div>
          </div>
          <div className="border-t border-separator-light pt-3">
            <p className="text-xs text-label-tertiary mb-1">Site ID</p>
            <p className="text-sm font-mono text-label-secondary">{site.site_id}</p>
          </div>
          <div className="flex gap-3 pt-2">
            <button onClick={onClose}
              className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm">Cancel</button>
            <button onClick={handleSave} disabled={isSaving || !clinicName}
              className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50 text-sm">
              {isSaving ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      </GlassCard>
      </div>
    </div>
  );
};

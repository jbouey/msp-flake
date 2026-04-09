import React from 'react';
import { GlassCard } from '../../../components/shared';

export interface PortalLinkModalProps {
  portalLink: { url: string; token: string };
  onClose: () => void;
  showToast: (msg: string, type: 'success' | 'error') => void;
}

/**
 * Portal Link Modal — displays a client-shareable URL with copy-to-clipboard.
 */
export const PortalLinkModal: React.FC<PortalLinkModalProps> = ({ portalLink, onClose, showToast }) => {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <GlassCard className="w-full max-w-lg">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-label-primary">Client Portal Link</h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-fill-secondary rounded-ios transition-colors"
          >
            <svg className="w-5 h-5 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <p className="text-label-secondary text-sm mb-4">
          Share this link with your client to give them access to their compliance dashboard.
          The link does not expire.
        </p>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-tertiary mb-1">Portal URL</label>
            <div className="flex gap-2">
              <input
                type="text"
                value={portalLink.url}
                readOnly
                className="flex-1 px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light font-mono text-sm"
              />
              <button
                onClick={() => {
                  navigator.clipboard.writeText(portalLink.url);
                  showToast('Portal URL copied to clipboard', 'success');
                }}
                className="px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors"
              >
                Copy
              </button>
            </div>
          </div>
          <div className="pt-2 border-t border-separator-light">
            <p className="text-xs text-label-tertiary">
              <strong>Security note:</strong> This link provides read-only access to compliance reports and evidence.
              Generate a new link if you need to revoke access.
            </p>
          </div>
        </div>
        <div className="flex justify-end mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors"
          >
            Done
          </button>
        </div>
      </GlassCard>
    </div>
  );
};

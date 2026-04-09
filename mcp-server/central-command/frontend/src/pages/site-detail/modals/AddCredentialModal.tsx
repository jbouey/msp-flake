import React, { useState } from 'react';
import { GlassCard } from '../../../components/shared';

export interface AddCredentialModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { credential_type: string; credential_name: string; username?: string; password?: string; host?: string }) => void;
  isLoading: boolean;
}

/**
 * Add Credential Modal
 */
export const AddCredentialModal: React.FC<AddCredentialModalProps> = ({ isOpen, onClose, onSubmit, isLoading }) => {
  const [credType, setCredType] = useState('router');
  const [credName, setCredName] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [host, setHost] = useState('');

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      credential_type: credType,
      credential_name: credName,
      username: username || undefined,
      password: password || undefined,
      host: host || undefined,
    });
    // Reset form
    setCredName('');
    setUsername('');
    setPassword('');
    setHost('');
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <GlassCard className="w-full max-w-md">
        <h2 className="text-xl font-semibold mb-4">Add Credential</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Type</label>
            <select
              value={credType}
              onChange={(e) => setCredType(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
            >
              <option value="router">Router</option>
              <option value="active_directory">Active Directory</option>
              <option value="ehr">EHR System</option>
              <option value="backup">Backup Service</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Name *</label>
            <input
              type="text"
              value={credName}
              onChange={(e) => setCredName(e.target.value)}
              placeholder="e.g., Main Router, Domain Admin"
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Host/IP</label>
            <input
              type="text"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="192.168.1.1"
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
            />
          </div>
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!credName || isLoading}
              className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white disabled:opacity-50"
            >
              {isLoading ? 'Adding...' : 'Add Credential'}
            </button>
          </div>
        </form>
      </GlassCard>
    </div>
  );
};

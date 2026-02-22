import React, { useState } from 'react';

interface AddDeviceModalProps {
  siteId: string;
  apiEndpoint: string; // e.g. "/api/sites/{id}/devices/manual" or "/api/portal/site/{id}/devices"
  onSuccess: () => void;
  onClose: () => void;
  portalMode?: boolean; // Light theme for portal
}

export const AddDeviceModal: React.FC<AddDeviceModalProps> = ({
  apiEndpoint,
  onSuccess,
  onClose,
  portalMode = false,
}) => {
  const [hostname, setHostname] = useState('');
  const [ipAddress, setIpAddress] = useState('');
  const [deviceType, setDeviceType] = useState('workstation');
  const [osType, setOsType] = useState('linux');
  const [sshUsername, setSshUsername] = useState('root');
  const [authMethod, setAuthMethod] = useState<'password' | 'key'>('password');
  const [sshPassword, setSshPassword] = useState('');
  const [sshKey, setSshKey] = useState('');
  const [port, setPort] = useState(22);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!hostname.trim() || !ipAddress.trim()) {
      setError('Hostname and IP address are required.');
      return;
    }
    if (authMethod === 'password' && !sshPassword) {
      setError('SSH password is required.');
      return;
    }
    if (authMethod === 'key' && !sshKey) {
      setError('SSH key is required.');
      return;
    }

    setSubmitting(true);
    try {
      const body: Record<string, unknown> = {
        hostname: hostname.trim(),
        ip_address: ipAddress.trim(),
        device_type: deviceType,
        os_type: osType,
        ssh_username: sshUsername,
        port,
      };
      if (authMethod === 'password') {
        body.ssh_password = sshPassword;
      } else {
        body.ssh_key = sshKey;
      }

      const res = await fetch(apiEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || `Failed (${res.status})`);
      }

      onSuccess();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add device');
    } finally {
      setSubmitting(false);
    }
  };

  // Theme-aware classes
  const cardBg = portalMode ? 'bg-white' : 'bg-slate-800 border border-slate-700';
  const labelColor = portalMode ? 'text-slate-700' : 'text-slate-300';
  const inputBg = portalMode
    ? 'bg-slate-50 border-slate-300 text-slate-900 focus:border-blue-500'
    : 'bg-slate-700 border-slate-600 text-white focus:border-blue-400';
  const titleColor = portalMode ? 'text-slate-900' : 'text-white';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className={`w-full max-w-lg rounded-2xl shadow-xl ${cardBg} p-6 max-h-[90vh] overflow-y-auto`}>
        <h2 className={`text-lg font-semibold ${titleColor} mb-4`}>Join Device</h2>
        <p className={`text-sm ${portalMode ? 'text-slate-500' : 'text-slate-400'} mb-6`}>
          Register a standalone device for SSH-based compliance monitoring. The appliance will begin scanning on its next check-in.
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Hostname + IP */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>Hostname / IP</label>
              <input
                type="text"
                value={hostname}
                onChange={(e) => setHostname(e.target.value)}
                placeholder="server-01 or 192.168.1.100"
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                required
              />
            </div>
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>IP Address</label>
              <input
                type="text"
                value={ipAddress}
                onChange={(e) => setIpAddress(e.target.value)}
                placeholder="192.168.1.100"
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                required
              />
            </div>
          </div>

          {/* Device Type + OS */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>Device Type</label>
              <select
                value={deviceType}
                onChange={(e) => setDeviceType(e.target.value)}
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
              >
                <option value="workstation">Workstation</option>
                <option value="server">Server</option>
              </select>
            </div>
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>OS Type</label>
              <select
                value={osType}
                onChange={(e) => setOsType(e.target.value)}
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
              >
                <option value="linux">Linux</option>
                <option value="macos">macOS</option>
              </select>
            </div>
          </div>

          {/* SSH Username + Port */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>SSH Username</label>
              <input
                type="text"
                value={sshUsername}
                onChange={(e) => setSshUsername(e.target.value)}
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
              />
            </div>
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>SSH Port</label>
              <input
                type="number"
                value={port}
                onChange={(e) => setPort(parseInt(e.target.value) || 22)}
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
              />
            </div>
          </div>

          {/* Auth Method Toggle */}
          <div>
            <label className={`block text-sm font-medium ${labelColor} mb-2`}>Authentication</label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setAuthMethod('password')}
                className={`px-4 py-1.5 text-sm rounded-lg transition ${
                  authMethod === 'password'
                    ? 'bg-blue-600 text-white'
                    : portalMode
                      ? 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                      : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
                }`}
              >
                Password
              </button>
              <button
                type="button"
                onClick={() => setAuthMethod('key')}
                className={`px-4 py-1.5 text-sm rounded-lg transition ${
                  authMethod === 'key'
                    ? 'bg-blue-600 text-white'
                    : portalMode
                      ? 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                      : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
                }`}
              >
                SSH Key
              </button>
            </div>
          </div>

          {/* Password or Key */}
          {authMethod === 'password' ? (
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>SSH Password</label>
              <input
                type="password"
                value={sshPassword}
                onChange={(e) => setSshPassword(e.target.value)}
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                required
              />
            </div>
          ) : (
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>Private Key</label>
              <textarea
                value={sshKey}
                onChange={(e) => setSshKey(e.target.value)}
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                rows={4}
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none font-mono`}
                required
              />
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className={`px-4 py-2 text-sm rounded-lg transition ${
                portalMode
                  ? 'text-slate-600 hover:bg-slate-100'
                  : 'text-slate-400 hover:bg-slate-700'
              }`}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50"
            >
              {submitting ? 'Adding...' : 'Add Device'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default AddDeviceModal;

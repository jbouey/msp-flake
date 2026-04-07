import React, { useState } from 'react';

interface AddNetworkDeviceModalProps {
  siteId: string;
  apiEndpoint: string;
  onSuccess: () => void;
  onClose: () => void;
  portalMode?: boolean;
}

type MgmtProtocol = 'snmp' | 'ssh' | 'api';
type DeviceCategory = 'switch' | 'router' | 'firewall' | 'access_point' | 'other';

const CATEGORIES: { value: DeviceCategory; label: string }[] = [
  { value: 'switch', label: 'Switch' },
  { value: 'router', label: 'Router' },
  { value: 'firewall', label: 'Firewall' },
  { value: 'access_point', label: 'Access Point' },
  { value: 'other', label: 'Other' },
];

const VENDORS = [
  'cisco', 'ubiquiti', 'aruba', 'juniper', 'meraki',
  'fortinet', 'mikrotik', 'netgear', 'tp-link', 'other',
];

export const AddNetworkDeviceModal: React.FC<AddNetworkDeviceModalProps> = ({
  apiEndpoint,
  onSuccess,
  onClose,
  portalMode = false,
}) => {
  const [hostname, setHostname] = useState('');
  const [ipAddress, setIpAddress] = useState('');
  const [deviceCategory, setDeviceCategory] = useState<DeviceCategory>('switch');
  const [vendor, setVendor] = useState('');
  const [model, setModel] = useState('');
  const [mgmtProtocol, setMgmtProtocol] = useState<MgmtProtocol>('snmp');

  // SNMP fields
  const [snmpCommunity, setSnmpCommunity] = useState('');
  const [snmpVersion, setSnmpVersion] = useState('2c');
  const [snmpV3User, setSnmpV3User] = useState('');
  const [snmpV3AuthPass, setSnmpV3AuthPass] = useState('');
  const [snmpV3PrivPass, setSnmpV3PrivPass] = useState('');

  // SSH fields
  const [sshUsername, setSshUsername] = useState('admin');
  const [sshPassword, setSshPassword] = useState('');
  const [sshKey, setSshKey] = useState('');
  const [sshPort, setSshPort] = useState(22);

  // API fields
  const [apiUrl, setApiUrl] = useState('');
  const [apiToken, setApiToken] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!hostname.trim() || !ipAddress.trim()) {
      setError('Device name and IP address are required.');
      return;
    }

    // Validate protocol-specific fields
    if (mgmtProtocol === 'snmp' && snmpVersion === '2c' && !snmpCommunity) {
      setError('SNMP community string is required for v2c.');
      return;
    }
    if (mgmtProtocol === 'snmp' && snmpVersion === '3' && !snmpV3User) {
      setError('SNMPv3 username is required.');
      return;
    }
    if (mgmtProtocol === 'ssh' && !sshPassword && !sshKey) {
      setError('SSH password or key is required.');
      return;
    }
    if (mgmtProtocol === 'api' && !apiUrl) {
      setError('API URL is required.');
      return;
    }

    setSubmitting(true);
    try {
      const body: Record<string, unknown> = {
        hostname: hostname.trim(),
        ip_address: ipAddress.trim(),
        device_category: deviceCategory,
        vendor: vendor || undefined,
        model: model || undefined,
        mgmt_protocol: mgmtProtocol,
        remediation_mode: 'advisory',
      };

      if (mgmtProtocol === 'snmp') {
        body.snmp_version = snmpVersion;
        if (snmpVersion === '2c') body.snmp_community = snmpCommunity;
        if (snmpVersion === '3') {
          body.snmp_v3_user = snmpV3User;
          if (snmpV3AuthPass) body.snmp_v3_auth_pass = snmpV3AuthPass;
          if (snmpV3PrivPass) body.snmp_v3_priv_pass = snmpV3PrivPass;
        }
      } else if (mgmtProtocol === 'ssh') {
        body.ssh_username = sshUsername;
        body.ssh_port = sshPort;
        if (sshPassword) body.ssh_password = sshPassword;
        if (sshKey) body.ssh_key = sshKey;
      } else if (mgmtProtocol === 'api') {
        body.api_url = apiUrl;
        if (apiToken) body.api_token = apiToken;
      }

      const csrfToken = document.cookie
        .split('; ')
        .find(c => c.startsWith('csrf_token='))
        ?.split('=')[1] || '';

      const res = await fetch(apiEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken,
        },
        credentials: 'include',
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || `Failed (${res.status})`);
      }

      onSuccess();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add network device');
    } finally {
      setSubmitting(false);
    }
  };

  const cardBg = portalMode ? 'bg-background-secondary' : 'bg-background-secondary border border-separator-light';
  const labelColor = portalMode ? 'text-label-primary' : 'text-label-secondary';
  const inputBg = portalMode
    ? 'bg-fill-tertiary border-separator-medium text-label-primary focus:border-blue-500'
    : 'bg-fill-secondary border-separator-light text-label-primary focus:border-blue-400';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className={`w-full max-w-lg rounded-2xl shadow-xl ${cardBg} p-6 max-h-[90vh] overflow-y-auto`}>
        <h2 className="text-lg font-semibold text-label-primary mb-1">Add Network Device</h2>
        <p className="text-sm text-label-tertiary mb-5">
          Register a switch, router, firewall, or AP for read-only compliance monitoring.
          Network devices are <strong>never auto-remediated</strong> — L2 generates advisory commands for human execution.
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name + IP */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>Device Name</label>
              <input
                type="text"
                value={hostname}
                onChange={(e) => setHostname(e.target.value)}
                placeholder="Core Switch 1"
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                required
              />
            </div>
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>Management IP</label>
              <input
                type="text"
                value={ipAddress}
                onChange={(e) => setIpAddress(e.target.value)}
                placeholder="192.168.1.1"
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                required
              />
            </div>
          </div>

          {/* Category + Vendor */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>Device Type</label>
              <select
                value={deviceCategory}
                onChange={(e) => setDeviceCategory(e.target.value as DeviceCategory)}
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
              >
                {CATEGORIES.map((c) => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={`block text-sm font-medium ${labelColor} mb-1`}>Vendor</label>
              <select
                value={vendor}
                onChange={(e) => setVendor(e.target.value)}
                className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
              >
                <option value="">Select vendor...</option>
                {VENDORS.map((v) => (
                  <option key={v} value={v}>{v.charAt(0).toUpperCase() + v.slice(1)}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Model */}
          <div>
            <label className={`block text-sm font-medium ${labelColor} mb-1`}>Model (optional)</label>
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="USW-48-PoE, SG350-28, etc."
              className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
            />
          </div>

          {/* Management Protocol */}
          <div>
            <label className={`block text-sm font-medium ${labelColor} mb-2`}>Management Protocol</label>
            <div className="flex gap-2">
              {(['snmp', 'ssh', 'api'] as MgmtProtocol[]).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setMgmtProtocol(p)}
                  className={`px-4 py-1.5 text-sm rounded-lg transition ${
                    mgmtProtocol === p
                      ? 'bg-blue-600 text-white'
                      : portalMode
                        ? 'bg-fill-secondary text-label-secondary hover:bg-fill-primary'
                        : 'bg-fill-secondary text-label-tertiary hover:bg-fill-primary'
                  }`}
                >
                  {p.toUpperCase()}
                </button>
              ))}
            </div>
          </div>

          {/* SNMP fields */}
          {mgmtProtocol === 'snmp' && (
            <div className="space-y-3 p-3 rounded-lg bg-fill-secondary/50">
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className={`block text-xs font-medium ${labelColor} mb-1`}>SNMP Version</label>
                  <select
                    value={snmpVersion}
                    onChange={(e) => setSnmpVersion(e.target.value)}
                    className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                  >
                    <option value="2c">v2c</option>
                    <option value="3">v3</option>
                  </select>
                </div>
                {snmpVersion === '2c' && (
                  <div className="flex-[2]">
                    <label className={`block text-xs font-medium ${labelColor} mb-1`}>Community String</label>
                    <input
                      type="password"
                      value={snmpCommunity}
                      onChange={(e) => setSnmpCommunity(e.target.value)}
                      placeholder="public"
                      className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                    />
                  </div>
                )}
              </div>
              {snmpVersion === '3' && (
                <>
                  <div>
                    <label className={`block text-xs font-medium ${labelColor} mb-1`}>Username</label>
                    <input
                      type="text"
                      value={snmpV3User}
                      onChange={(e) => setSnmpV3User(e.target.value)}
                      className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className={`block text-xs font-medium ${labelColor} mb-1`}>Auth Password</label>
                      <input
                        type="password"
                        value={snmpV3AuthPass}
                        onChange={(e) => setSnmpV3AuthPass(e.target.value)}
                        className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                      />
                    </div>
                    <div>
                      <label className={`block text-xs font-medium ${labelColor} mb-1`}>Privacy Password</label>
                      <input
                        type="password"
                        value={snmpV3PrivPass}
                        onChange={(e) => setSnmpV3PrivPass(e.target.value)}
                        className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                      />
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

          {/* SSH fields */}
          {mgmtProtocol === 'ssh' && (
            <div className="space-y-3 p-3 rounded-lg bg-fill-secondary/50">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={`block text-xs font-medium ${labelColor} mb-1`}>Username</label>
                  <input
                    type="text"
                    value={sshUsername}
                    onChange={(e) => setSshUsername(e.target.value)}
                    className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                  />
                </div>
                <div>
                  <label className={`block text-xs font-medium ${labelColor} mb-1`}>Port</label>
                  <input
                    type="number"
                    value={sshPort}
                    onChange={(e) => setSshPort(parseInt(e.target.value) || 22)}
                    className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                  />
                </div>
              </div>
              <div>
                <label className={`block text-xs font-medium ${labelColor} mb-1`}>Password</label>
                <input
                  type="password"
                  value={sshPassword}
                  onChange={(e) => setSshPassword(e.target.value)}
                  className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                />
              </div>
              <div>
                <label className={`block text-xs font-medium ${labelColor} mb-1`}>Private Key (optional)</label>
                <textarea
                  value={sshKey}
                  onChange={(e) => setSshKey(e.target.value)}
                  placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                  rows={3}
                  className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none font-mono`}
                />
              </div>
            </div>
          )}

          {/* API fields */}
          {mgmtProtocol === 'api' && (
            <div className="space-y-3 p-3 rounded-lg bg-fill-secondary/50">
              <div>
                <label className={`block text-xs font-medium ${labelColor} mb-1`}>API URL</label>
                <input
                  type="url"
                  value={apiUrl}
                  onChange={(e) => setApiUrl(e.target.value)}
                  placeholder="https://192.168.1.1/api"
                  className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                />
              </div>
              <div>
                <label className={`block text-xs font-medium ${labelColor} mb-1`}>API Token / Key</label>
                <input
                  type="password"
                  value={apiToken}
                  onChange={(e) => setApiToken(e.target.value)}
                  className={`w-full px-3 py-2 text-sm rounded-lg border ${inputBg} outline-none`}
                />
              </div>
            </div>
          )}

          {/* Safety notice */}
          <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <svg className="w-4 h-4 text-amber-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.072 16.5c-.77.833.192 2.5 1.732 2.5z" />
            </svg>
            <p className="text-xs text-label-secondary">
              <strong className="text-amber-600">Read-only monitoring.</strong> The appliance will poll this device for configuration and status but will never push changes.
              If an issue is detected, L2 will generate advisory remediation commands in the escalation ticket for human execution.
            </p>
          </div>

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
                  ? 'text-label-secondary hover:bg-fill-secondary'
                  : 'text-label-tertiary hover:bg-fill-secondary'
              }`}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition disabled:opacity-50"
            >
              {submitting ? 'Adding...' : 'Add Network Device'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default AddNetworkDeviceModal;

import React, { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { AddDeviceModal } from '../components/shared/AddDeviceModal';
import { useSiteDevices, useSiteDeviceSummary } from '../hooks';
import type { DiscoveredDevice, SiteDeviceSummary as DeviceSummaryType } from '../utils/api';

/**
 * Format relative time
 */
function formatRelativeTime(dateString: string | null | undefined): string {
  if (!dateString) return 'Never';

  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins} min ago`;

  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

/**
 * Device type icons and labels
 */
const deviceTypeConfig: Record<string, { icon: string; label: string; color: string }> = {
  workstation: { icon: '💻', label: 'Workstation', color: 'text-blue-400' },
  server: { icon: '🖥️', label: 'Server', color: 'text-purple-400' },
  network: { icon: '🔌', label: 'Network', color: 'text-orange-400' },
  printer: { icon: '🖨️', label: 'Printer', color: 'text-slate-400' },
  medical: { icon: '🏥', label: 'Medical', color: 'text-red-400' },
  unknown: { icon: '❓', label: 'Unknown', color: 'text-slate-500' },
};

/**
 * Compliance status colors
 */
const complianceColors: Record<string, string> = {
  compliant: 'bg-health-healthy text-white',
  drifted: 'bg-health-critical text-white',
  unknown: 'bg-slate-500 text-white',
  excluded: 'bg-slate-600 text-white',
};

/**
 * Device summary card
 */
const SummaryCard: React.FC<{ summary: DeviceSummaryType }> = ({ summary }) => {
  const rateColor = summary.compliance_rate >= 80 ? 'text-health-healthy' :
                    summary.compliance_rate >= 50 ? 'text-health-warning' : 'text-health-critical';

  return (
    <GlassCard className="p-6 mb-6">
      <h2 className="text-lg font-semibold text-label-primary mb-4">Device Inventory Summary</h2>

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        {/* Overall compliance */}
        <div className="text-center">
          <div className={`text-3xl font-bold ${rateColor}`}>
            {summary.compliance_rate.toFixed(0)}%
          </div>
          <div className="text-sm text-label-secondary">Compliance</div>
        </div>

        {/* Total devices */}
        <div className="text-center">
          <div className="text-3xl font-bold text-label-primary">
            {summary.total_devices}
          </div>
          <div className="text-sm text-label-secondary">Total</div>
        </div>

        {/* Compliant */}
        <div className="text-center">
          <div className="text-3xl font-bold text-health-healthy">
            {summary.by_compliance.compliant}
          </div>
          <div className="text-sm text-label-secondary">Compliant</div>
        </div>

        {/* Drifted */}
        <div className="text-center">
          <div className="text-3xl font-bold text-health-critical">
            {summary.by_compliance.drifted}
          </div>
          <div className="text-sm text-label-secondary">Drifted</div>
        </div>

        {/* Unknown */}
        <div className="text-center">
          <div className="text-3xl font-bold text-slate-400">
            {summary.by_compliance.unknown}
          </div>
          <div className="text-sm text-label-secondary">Unknown</div>
        </div>

        {/* Medical */}
        <div className="text-center">
          <div className="text-3xl font-bold text-red-400">
            {summary.medical_devices.total}
          </div>
          <div className="text-sm text-label-secondary">Medical</div>
        </div>
      </div>

      {/* Device type breakdown */}
      <div className="mt-6 pt-4 border-t border-glass-border">
        <h3 className="text-sm font-medium text-label-secondary mb-3">Devices by Type</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="rounded-lg p-3 bg-blue-500/10 border border-blue-500/30 text-center">
            <div className="text-sm font-medium text-blue-400">Workstations</div>
            <div className="text-2xl font-bold text-label-primary">{summary.by_type.workstations}</div>
          </div>
          <div className="rounded-lg p-3 bg-purple-500/10 border border-purple-500/30 text-center">
            <div className="text-sm font-medium text-purple-400">Servers</div>
            <div className="text-2xl font-bold text-label-primary">{summary.by_type.servers}</div>
          </div>
          <div className="rounded-lg p-3 bg-orange-500/10 border border-orange-500/30 text-center">
            <div className="text-sm font-medium text-orange-400">Network</div>
            <div className="text-2xl font-bold text-label-primary">{summary.by_type.network}</div>
          </div>
          <div className="rounded-lg p-3 bg-slate-500/10 border border-slate-500/30 text-center">
            <div className="text-sm font-medium text-slate-400">Printers</div>
            <div className="text-2xl font-bold text-label-primary">{summary.by_type.printers}</div>
          </div>
        </div>
      </div>

      {/* Medical device notice */}
      {summary.medical_devices.total > 0 && (
        <div className="mt-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30">
          <div className="flex items-center gap-2">
            <span className="text-lg">⚠️</span>
            <div className="text-sm text-red-400">
              <strong>{summary.medical_devices.total} medical device(s) detected</strong> - Excluded from compliance scanning by default for patient safety.
              {summary.medical_devices.excluded_by_default && ' Manual opt-in required for monitoring.'}
            </div>
          </div>
        </div>
      )}
    </GlassCard>
  );
};

/**
 * Single device row
 */
const DeviceRow: React.FC<{
  device: DiscoveredDevice;
  expanded: boolean;
  onToggle: () => void;
}> = ({ device, expanded, onToggle }) => {
  const typeConfig = deviceTypeConfig[device.device_type] || deviceTypeConfig.unknown;
  const complianceColor = complianceColors[device.compliance_status] || complianceColors.unknown;

  return (
    <>
      <tr
        className="hover:bg-glass-bg/30 cursor-pointer transition-colors"
        onClick={onToggle}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-lg">{typeConfig.icon}</span>
            <div>
              <span className="font-medium text-label-primary">{device.hostname || device.ip_address}</span>
              {device.medical_device && (
                <Badge variant="error" className="ml-2 text-xs">Medical</Badge>
              )}
            </div>
          </div>
        </td>
        <td className="px-4 py-3 text-label-secondary font-mono text-sm">
          {device.ip_address}
        </td>
        <td className="px-4 py-3 text-label-secondary font-mono text-sm">
          {device.mac_address || '-'}
        </td>
        <td className="px-4 py-3">
          <span className={`${typeConfig.color} text-sm`}>
            {typeConfig.label}
          </span>
        </td>
        <td className="px-4 py-3 text-label-secondary text-sm">
          {device.os_name || '-'}
        </td>
        <td className="px-4 py-3">
          <span className={`px-2 py-1 rounded-full text-xs font-medium ${complianceColor}`}>
            {device.compliance_status}
          </span>
        </td>
        <td className="px-4 py-3 text-label-secondary text-sm">
          {formatRelativeTime(device.last_seen_at)}
        </td>
        <td className="px-4 py-3 text-right">
          <svg
            className={`w-5 h-5 text-label-tertiary transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </td>
      </tr>

      {/* Expanded details */}
      {expanded && (
        <tr>
          <td colSpan={8} className="px-4 py-4 bg-glass-bg/20">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <span className="text-label-tertiary">OS Version:</span>
                <div className="text-label-primary font-medium">{device.os_version || 'Unknown'}</div>
              </div>
              <div>
                <span className="text-label-tertiary">Discovery Source:</span>
                <div className="text-label-primary font-medium">{device.discovery_source}</div>
              </div>
              <div>
                <span className="text-label-tertiary">First Seen:</span>
                <div className="text-label-primary font-medium">{formatRelativeTime(device.first_seen_at)}</div>
              </div>
              <div>
                <span className="text-label-tertiary">Appliance:</span>
                <div className="text-label-primary font-medium">{device.appliance_hostname}</div>
              </div>
              <div>
                <span className="text-label-tertiary">Scan Policy:</span>
                <div className="text-label-primary font-medium">{device.scan_policy}</div>
              </div>
              <div>
                <span className="text-label-tertiary">Last Scan:</span>
                <div className="text-label-primary font-medium">{formatRelativeTime(device.last_scan_at)}</div>
              </div>
              {device.open_ports && device.open_ports.length > 0 && (
                <div className="col-span-2">
                  <span className="text-label-tertiary">Open Ports:</span>
                  <div className="text-label-primary font-mono text-xs mt-1">
                    {device.open_ports.join(', ')}
                  </div>
                </div>
              )}
            </div>
            {device.medical_device && (
              <div className="mt-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30">
                <div className="flex items-start gap-2">
                  <span className="text-lg">🏥</span>
                  <div className="text-sm">
                    <div className="font-medium text-red-400">Medical Device</div>
                    <div className="text-label-secondary mt-1">
                      This device is classified as medical equipment.
                      {device.manually_opted_in
                        ? ' Monitoring enabled via manual opt-in.'
                        : ' Excluded from compliance scanning to prevent interference with patient care.'}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
};

/**
 * Device list table
 */
const DeviceTable: React.FC<{
  devices: DiscoveredDevice[];
  siteId: string;
  onFilterChange: (filter: { type?: string; status?: string; includeMedical: boolean }) => void;
  filter: { type?: string; status?: string; includeMedical: boolean };
}> = ({ devices, filter, onFilterChange }) => {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  return (
    <GlassCard className="overflow-hidden">
      {/* Filter bar */}
      <div className="flex flex-wrap gap-4 p-4 border-b border-glass-border items-center">
        {/* Type filter */}
        <div className="flex items-center gap-2">
          <span className="text-sm text-label-tertiary">Type:</span>
          <select
            value={filter.type || ''}
            onChange={(e) => onFilterChange({ ...filter, type: e.target.value || undefined })}
            className="px-2 py-1 rounded bg-glass-bg border border-glass-border text-sm text-label-primary"
          >
            <option value="">All Types</option>
            <option value="workstation">Workstation</option>
            <option value="server">Server</option>
            <option value="network">Network</option>
            <option value="printer">Printer</option>
          </select>
        </div>

        {/* Compliance filter */}
        <div className="flex items-center gap-2">
          <span className="text-sm text-label-tertiary">Status:</span>
          <select
            value={filter.status || ''}
            onChange={(e) => onFilterChange({ ...filter, status: e.target.value || undefined })}
            className="px-2 py-1 rounded bg-glass-bg border border-glass-border text-sm text-label-primary"
          >
            <option value="">All Status</option>
            <option value="compliant">Compliant</option>
            <option value="drifted">Drifted</option>
            <option value="unknown">Unknown</option>
          </select>
        </div>

        {/* Medical device toggle */}
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={filter.includeMedical}
            onChange={(e) => onFilterChange({ ...filter, includeMedical: e.target.checked })}
            className="rounded border-glass-border"
          />
          <span className="text-sm text-label-secondary">Include Medical Devices</span>
        </label>

        <div className="flex-1" />

        <span className="text-sm text-label-tertiary">
          {devices.length} device{devices.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-glass-border">
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Device</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">IP Address</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">MAC Address</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Type</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">OS</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Status</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Last Seen</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-label-secondary"></th>
            </tr>
          </thead>
          <tbody>
            {devices.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-label-tertiary">
                  No devices discovered yet. Devices will appear after the network scanner runs.
                </td>
              </tr>
            ) : (
              devices.map((device) => (
                <DeviceRow
                  key={device.id}
                  device={device}
                  expanded={expandedId === device.id}
                  onToggle={() => setExpandedId(expandedId === device.id ? null : device.id)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
};

/**
 * Site Devices Page
 */
export const SiteDevices: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const [filter, setFilter] = useState<{ type?: string; status?: string; includeMedical: boolean }>({
    includeMedical: false,
  });
  const [showAddDevice, setShowAddDevice] = useState(false);

  const { data: devicesData, isLoading: devicesLoading, error: devicesError } = useSiteDevices(
    siteId || null,
    {
      device_type: filter.type,
      compliance_status: filter.status,
      include_medical: filter.includeMedical,
    }
  );

  const { data: summary, isLoading: summaryLoading } = useSiteDeviceSummary(siteId || null);

  const isLoading = devicesLoading || summaryLoading;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size="lg" />
      </div>
    );
  }

  if (devicesError) {
    return (
      <GlassCard className="p-6">
        <div className="text-center text-health-critical">
          <p>Failed to load devices</p>
          <p className="text-sm text-label-secondary mt-2">{devicesError.message}</p>
        </div>
      </GlassCard>
    );
  }

  const devices = devicesData?.devices || [];

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm">
        <Link to="/sites" className="text-label-secondary hover:text-label-primary">
          Sites
        </Link>
        <span className="text-label-tertiary">/</span>
        <Link to={`/sites/${siteId}`} className="text-label-secondary hover:text-label-primary">
          {siteId}
        </Link>
        <span className="text-label-tertiary">/</span>
        <span className="text-label-primary">Devices</span>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-label-primary">Device Inventory</h1>
          <p className="text-label-secondary mt-1">
            Network-discovered devices from the appliance scanner
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowAddDevice(true)}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-accent-primary text-white hover:bg-accent-primary/90 transition flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Join Device
          </button>
          <Badge variant="info" className="px-3 py-1">
            {devices.length} Devices
          </Badge>
        </div>
      </div>

      {/* Add Device Modal */}
      {showAddDevice && siteId && (
        <AddDeviceModal
          siteId={siteId}
          apiEndpoint={`/api/sites/${siteId}/devices/manual`}
          onSuccess={() => {
            setShowAddDevice(false);
            window.location.reload();
          }}
          onClose={() => setShowAddDevice(false)}
        />
      )}

      {/* Info banner */}
      <GlassCard className="p-4 bg-accent-primary/5 border-accent-primary/20">
        <div className="flex items-start gap-3">
          <svg className="w-5 h-5 text-accent-primary mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div>
            <h3 className="text-sm font-medium text-label-primary">Network Scanner (EYES)</h3>
            <p className="text-sm text-label-secondary mt-1">
              Devices are discovered via AD enumeration, ARP scanning, and nmap probing. The scanner runs daily at 2 AM.
              Medical devices are excluded from compliance scanning by default for patient safety.
            </p>
          </div>
        </div>
      </GlassCard>

      {/* Summary */}
      {summary && <SummaryCard summary={summary} />}

      {/* Device list */}
      <DeviceTable
        devices={devices}
        siteId={siteId || ''}
        filter={filter}
        onFilterChange={setFilter}
      />
    </div>
  );
};

export default SiteDevices;

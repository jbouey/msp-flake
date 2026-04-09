import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { GlassCard, Spinner, Badge, OrgBanner } from '../components/shared';
import { AddDeviceModal } from '../components/shared/AddDeviceModal';
import { AddNetworkDeviceModal } from '../components/shared/AddNetworkDeviceModal';
import { useSiteDevices, useSiteDeviceSummary } from '../hooks';
import type { DiscoveredDevice, SiteDeviceSummary as DeviceSummaryType } from '../utils/api';
import { CHECK_TYPE_LABELS } from '../types';
import { formatTimeAgo } from '../constants';

const formatRelativeTime = formatTimeAgo;

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
  drifted: 'bg-orange-500 text-white',
  unknown: 'bg-slate-500 text-white',
  excluded: 'bg-slate-600 text-white',
};

const complianceLabels: Record<string, string> = {
  compliant: 'Passing',
  drifted: 'Failing',
  unknown: 'No Data',
  excluded: 'Excluded',
};

/**
 * Device summary card
 */
const SummaryCard: React.FC<{ summary: DeviceSummaryType }> = ({ summary }) => {
  const compliant = summary.by_compliance?.compliant ?? 0;
  const failing = summary.by_compliance?.drifted ?? 0;
  const managed = compliant + failing;
  const unknown = summary.by_compliance?.unknown ?? 0;
  const managedRate = managed > 0 ? Math.round(compliant / managed * 100) : 0;
  const managedRateColor = managedRate >= 80 ? 'text-health-healthy' :
                           managedRate >= 50 ? 'text-health-warning' : 'text-health-critical';

  return (
    <GlassCard className="p-6 mb-6">
      {/* Managed Fleet — the compliance story */}
      <div className="mb-6">
        <h2 className="text-lg font-semibold text-label-primary mb-4">Managed Fleet</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="text-center">
            <div className={`text-3xl font-bold ${managedRateColor}`}>
              {managedRate}%
            </div>
            <div className="text-sm text-label-secondary">Compliance Rate</div>
          </div>
          <div className="text-center">
            <div className="text-3xl font-bold text-label-primary">
              {managed}
            </div>
            <div className="text-sm text-label-secondary">Scanned</div>
          </div>
          <div className="text-center">
            <div className="text-3xl font-bold text-health-healthy">
              {compliant}
            </div>
            <div className="text-sm text-label-secondary">Passing</div>
          </div>
          <div className="text-center">
            <div className="text-3xl font-bold text-orange-500">
              {failing}
            </div>
            <div className="text-sm text-label-secondary">Failing</div>
          </div>
        </div>
      </div>

      {/* Network Discovery — context */}
      <div className="pt-4 border-t border-glass-border">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-label-secondary">Network Discovery</h3>
          <span className="text-xs text-label-tertiary">{summary.total_devices} devices found on subnet</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div className="rounded-lg p-3 bg-blue-500/10 border border-blue-500/30 text-center">
            <div className="text-sm font-medium text-blue-400">Workstations</div>
            <div className="text-2xl font-bold text-label-primary">{summary.by_type?.workstations ?? 0}</div>
          </div>
          <div className="rounded-lg p-3 bg-purple-500/10 border border-purple-500/30 text-center">
            <div className="text-sm font-medium text-purple-400">Servers</div>
            <div className="text-2xl font-bold text-label-primary">{summary.by_type?.servers ?? 0}</div>
          </div>
          <div className="rounded-lg p-3 bg-orange-500/10 border border-orange-500/30 text-center">
            <div className="text-sm font-medium text-orange-400">Network</div>
            <div className="text-2xl font-bold text-label-primary">{summary.by_type?.network ?? 0}</div>
          </div>
          <div className="rounded-lg p-3 bg-slate-500/10 border border-slate-500/30 text-center">
            <div className="text-sm font-medium text-slate-400">Printers</div>
            <div className="text-2xl font-bold text-label-primary">{summary.by_type?.printers ?? 0}</div>
          </div>
          <div className="rounded-lg p-3 bg-slate-500/10 border border-slate-500/20 text-center">
            <div className="text-sm font-medium text-slate-500">Unscanned</div>
            <div className="text-2xl font-bold text-slate-400">{unknown}</div>
          </div>
        </div>
        {summary.coverage && summary.coverage.agents_enrolled > 0 && (
          <div className="mt-3 text-xs text-label-tertiary">
            {summary.coverage.agents_enrolled} agent{summary.coverage.agents_enrolled !== 1 ? 's' : ''} deployed
          </div>
        )}
      </div>

      {/* Medical device notice */}
      {(summary.medical_devices?.total ?? 0) > 0 && (
        <div className="mt-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30">
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-red-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
            </svg>
            <div className="text-sm text-red-400">
              <strong>{summary.medical_devices?.total ?? 0} medical device(s) detected</strong> — excluded from compliance scanning for patient safety.
              {summary.medical_devices?.excluded_by_default && ' Manual opt-in required.'}
            </div>
          </div>
        </div>
      )}
    </GlassCard>
  );
};

/**
 * Coverage tier badge — driven by device_status
 */
const coverageLevels: Record<string, { label: string; color: string; bg: string }> = {
  agent_active: { label: 'Agent', color: 'text-emerald-400', bg: 'bg-emerald-500/15' },
  deploying: { label: 'Deploying...', color: 'text-amber-400', bg: 'bg-amber-500/15' },
  deploy_failed: { label: 'Failed', color: 'text-red-400', bg: 'bg-red-500/15' },
  ad_managed: { label: 'AD — auto-deploy', color: 'text-blue-400', bg: 'bg-blue-500/15' },
  take_over_available: { label: 'Take Over', color: 'text-yellow-400', bg: 'bg-yellow-500/15' },
  pending_deploy: { label: 'Pending...', color: 'text-amber-400', bg: 'bg-amber-500/15' },
  ignored: { label: 'Ignored', color: 'text-slate-500', bg: 'bg-slate-500/10' },
  discovered: { label: 'New', color: 'text-slate-400', bg: 'bg-slate-500/10' },
  probed: { label: 'Probed', color: 'text-slate-400', bg: 'bg-slate-500/10' },
  agent_stale: { label: 'Stale', color: 'text-orange-400', bg: 'bg-orange-500/15' },
  agent_offline: { label: 'Offline', color: 'text-red-400', bg: 'bg-red-500/15' },
  archived: { label: 'Archived', color: 'text-slate-600', bg: 'bg-slate-500/10' },
};

const CoverageTierCell: React.FC<{
  device: DiscoveredDevice;
  onTakeOver: (device: DiscoveredDevice) => void;
}> = ({ device, onTakeOver }) => {
  const status = device.device_status || 'discovered';
  const level = coverageLevels[status] || coverageLevels.discovered;

  if (device.device_status === 'take_over_available') {
    return (
      <button
        onClick={(e) => { e.stopPropagation(); onTakeOver(device); }}
        className="px-2 py-1 text-xs font-medium rounded bg-yellow-500/15 text-yellow-400 hover:bg-yellow-500/25 transition-colors"
      >
        Take Over
      </button>
    );
  }

  return (
    <span className={`px-2 py-1 text-xs font-medium rounded ${level.bg} ${level.color}`}>
      {level.label}
    </span>
  );
};

/**
 * Compliance check status colors
 */
const checkStatusColors: Record<string, string> = {
  pass: 'text-health-healthy',
  warn: 'text-health-warning',
  fail: 'text-health-critical',
};

interface ComplianceDetail {
  device_id: number;
  hostname: string;
  ip_address: string;
  compliance_status: string;
  checks: Array<{
    check_type: string;
    hipaa_control: string;
    status: string;
    details: Record<string, unknown>;
    checked_at: string;
  }>;
  total_checks: number;
  passed: number;
  warned: number;
  failed: number;
}

/**
 * Single device row
 */
const DeviceRow: React.FC<{
  device: DiscoveredDevice;
  siteId: string;
  expanded: boolean;
  onToggle: () => void;
  onTakeOver: (device: DiscoveredDevice) => void;
}> = ({ device, siteId, expanded, onToggle, onTakeOver }) => {
  const typeConfig = deviceTypeConfig[device.device_type] || deviceTypeConfig.unknown;
  const complianceColor = complianceColors[device.compliance_status] || complianceColors.unknown;
  const [complianceData, setComplianceData] = useState<ComplianceDetail | null>(null);
  const [complianceLoading, setComplianceLoading] = useState(false);
  const [incidents, setIncidents] = useState<Array<{ id: string; check_type: string; severity: string; resolved: boolean; created_at: string }>>([]);

  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;

    // Fetch compliance details
    setComplianceLoading(true);
    fetch(`/api/devices/sites/${siteId}/device/${device.id}/compliance`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (!cancelled) setComplianceData(data); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setComplianceLoading(false); });

    // Fetch incidents for this device's hostname/IP
    const hostname = device.hostname || device.ip_address;
    fetch(`/api/dashboard/incidents?site_id=${siteId}&limit=10`)
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        if (cancelled) return;
        // Filter incidents matching this device by hostname or IP
        const matching = (data || []).filter((inc: { hostname: string }) =>
          inc.hostname === hostname ||
          inc.hostname === device.ip_address ||
          inc.hostname === device.hostname
        );
        setIncidents(matching);
      })
      .catch(() => {});

    return () => { cancelled = true; };
  }, [expanded, siteId, device.id, device.hostname, device.ip_address]);

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
          <div>{device.mac_address || '-'}</div>
          {device.manufacturer_hint?.manufacturer && (
            <div className="text-xs italic text-label-tertiary mt-0.5" title="Inferred from MAC address (OUI)">
              {device.manufacturer_hint.manufacturer}
            </div>
          )}
        </td>
        <td className="px-4 py-3">
          <span className={`${typeConfig.color} text-sm`}>
            {typeConfig.label}
          </span>
          {device.manufacturer_hint?.device_class && device.device_type === 'unknown' && (
            <div className="text-xs italic text-label-tertiary mt-0.5" title="Suggested type from MAC OUI lookup — not confirmed">
              hint: {device.manufacturer_hint.device_class}
            </div>
          )}
        </td>
        <td className="px-4 py-3 text-label-secondary text-sm">
          {device.os_name || '-'}
        </td>
        <td className="px-4 py-3">
          <span className={`px-2 py-1 rounded-full text-xs font-medium ${complianceColor}`}>
            {complianceLabels[device.compliance_status] || device.compliance_status}
          </span>
        </td>
        <td className="px-4 py-3">
          <CoverageTierCell device={device} onTakeOver={onTakeOver} />
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
          <td colSpan={9} className="px-4 py-4 bg-glass-bg/20">
            {/* Basic metadata */}
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
              {device.manufacturer_hint?.manufacturer && (
                <div>
                  <span className="text-label-tertiary">Manufacturer:</span>
                  <div className="text-label-primary font-medium italic" title="Inferred from MAC address OUI — not guaranteed">
                    {device.manufacturer_hint.manufacturer}
                    {device.manufacturer_hint.device_class && (
                      <span className="text-label-tertiary ml-1">({device.manufacturer_hint.device_class})</span>
                    )}
                  </div>
                </div>
              )}
              <div>
                <span className="text-label-tertiary">Scan Policy:</span>
                <div className="text-label-primary font-medium">{device.scan_policy}</div>
              </div>
              {device.agent_coverage && device.agent_coverage.level === 'agent' && (
                <div>
                  <span className="text-label-tertiary">Agent Version:</span>
                  <div className="text-label-primary font-medium">
                    {device.agent_coverage.agent_version || 'Unknown'}
                    <span className={`ml-2 text-xs ${device.agent_coverage.agent_status === 'connected' ? 'text-health-healthy' : 'text-health-warning'}`}>
                      ({device.agent_coverage.agent_status || 'unknown'})
                    </span>
                  </div>
                </div>
              )}
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

            {/* Compliance Checks */}
            <div className="mt-4 pt-4 border-t border-glass-border">
              <h4 className="text-sm font-medium text-label-primary mb-3">Compliance Checks</h4>
              {complianceLoading ? (
                <div className="flex items-center gap-2 text-label-tertiary text-sm">
                  <Spinner size="sm" /> Loading checks...
                </div>
              ) : complianceData && complianceData.checks && complianceData.checks.length > 0 ? (
                <div className="space-y-2">
                  <div className="flex gap-4 text-xs text-label-tertiary mb-2">
                    <span className="text-health-healthy">{complianceData.passed} passed</span>
                    <span className="text-health-warning">{complianceData.warned} warnings</span>
                    <span className="text-health-critical">{complianceData.failed} failed</span>
                  </div>
                  <div className="grid gap-1">
                    {complianceData.checks.map((check, i) => (
                      <div key={i} className="flex items-center gap-3 px-3 py-2 rounded bg-glass-bg/30 text-sm">
                        <span className={`font-medium ${checkStatusColors[check.status] || 'text-label-secondary'}`}>
                          {check.status === 'pass' ? 'PASS' : check.status === 'warn' ? 'WARN' : 'FAIL'}
                        </span>
                        <span className="text-label-primary flex-1">
                          {CHECK_TYPE_LABELS[check.check_type] || check.check_type}
                        </span>
                        <span className="text-label-tertiary text-xs font-mono">
                          {check.hipaa_control}
                        </span>
                        <span className="text-label-tertiary text-xs">
                          {formatRelativeTime(check.checked_at)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-label-tertiary">No compliance checks recorded for this device.</p>
              )}
            </div>

            {/* Related Incidents */}
            {incidents.length > 0 && (
              <div className="mt-4 pt-4 border-t border-glass-border">
                <h4 className="text-sm font-medium text-label-primary mb-3">
                  Related Incidents ({incidents.length})
                </h4>
                <div className="grid gap-1">
                  {incidents.map((inc) => (
                    <Link
                      key={inc.id}
                      to={`/incidents?highlight=${inc.id}`}
                      className="flex items-center gap-3 px-3 py-2 rounded bg-glass-bg/30 text-sm hover:bg-glass-bg/50 transition-colors"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <span className={`w-2 h-2 rounded-full ${inc.resolved ? 'bg-health-healthy' : 'bg-health-warning animate-pulse'}`} />
                      <span className="text-label-primary flex-1">
                        {CHECK_TYPE_LABELS[inc.check_type] || inc.check_type}
                      </span>
                      <span className={`text-xs ${inc.resolved ? 'text-health-healthy' : 'text-health-warning'}`}>
                        {inc.resolved ? 'Resolved' : 'Active'}
                      </span>
                      <span className="text-label-tertiary text-xs">
                        {formatRelativeTime(inc.created_at)}
                      </span>
                    </Link>
                  ))}
                </div>
              </div>
            )}

            {/* Failing device call-to-action */}
            {device.compliance_status === 'drifted' && incidents.length === 0 && (
              <div className="mt-4 p-3 rounded-lg bg-health-warning/10 border border-health-warning/30">
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-health-warning font-medium">Compliance Issue Detected</span>
                  <span className="text-label-secondary">
                    — This device has a compliance issue but no matching incidents found. The issue may have been detected by the network scanner but not yet reported as an incident.
                  </span>
                </div>
              </div>
            )}

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
  onTakeOver: (device: DiscoveredDevice) => void;
}> = ({ devices, siteId, filter, onFilterChange, onTakeOver }) => {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [devPage, setDevPage] = useState(0);
  const devPageSize = 25;

  const MANAGED_STATUSES = ['agent_active', 'ad_managed', 'deploying', 'pending_deploy', 'agent_stale'];
  const managedCount = devices.filter((d) => MANAGED_STATUSES.includes(d.device_status || '')).length;
  const filteredDevices = showAll
    ? devices
    : devices.filter((d) => MANAGED_STATUSES.includes(d.device_status || ''));

  const totalDevFiltered = filteredDevices.length;
  const totalDevPages = Math.ceil(totalDevFiltered / devPageSize);
  const paginatedDevices = filteredDevices.slice(devPage * devPageSize, (devPage + 1) * devPageSize);
  const devStart = devPage * devPageSize + 1;
  const devEnd = Math.min((devPage + 1) * devPageSize, totalDevFiltered);

  return (
    <GlassCard className="overflow-hidden">
      {/* Managed / All toggle */}
      <div className="flex items-center gap-2 px-4 pt-4 mb-0">
        <button
          onClick={() => { setShowAll(false); setDevPage(0); }}
          className={`px-3 py-1 text-xs rounded ${!showAll ? 'bg-blue-500/20 text-blue-400' : 'text-slate-400 hover:text-slate-300'}`}
        >
          Managed ({managedCount})
        </button>
        <button
          onClick={() => { setShowAll(true); setDevPage(0); }}
          className={`px-3 py-1 text-xs rounded ${showAll ? 'bg-blue-500/20 text-blue-400' : 'text-slate-400 hover:text-slate-300'}`}
        >
          All Devices ({devices.length})
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-4 p-4 border-b border-glass-border items-center">
        {/* Type filter */}
        <div className="flex items-center gap-2">
          <span className="text-sm text-label-tertiary">Type:</span>
          <select
            value={filter.type || ''}
            onChange={(e) => { setDevPage(0); onFilterChange({ ...filter, type: e.target.value || undefined }); }}
            className="px-2 py-1 rounded bg-background-secondary border border-glass-border text-sm text-label-primary [color-scheme:dark]"
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
            onChange={(e) => { setDevPage(0); onFilterChange({ ...filter, status: e.target.value || undefined }); }}
            className="px-2 py-1 rounded bg-background-secondary border border-glass-border text-sm text-label-primary [color-scheme:dark]"
          >
            <option value="">All Status</option>
            <option value="compliant">Passing</option>
            <option value="drifted">Failing</option>
            <option value="unknown">No Data</option>
          </select>
        </div>

        {/* Medical device toggle */}
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={filter.includeMedical}
            onChange={(e) => { setDevPage(0); onFilterChange({ ...filter, includeMedical: e.target.checked }); }}
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
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Coverage</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Last Seen</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-label-secondary"></th>
            </tr>
          </thead>
          <tbody>
            {paginatedDevices.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-8 text-center text-label-tertiary">
                  {showAll
                    ? 'No devices discovered yet. Devices will appear after the network scanner runs.'
                    : 'No managed devices. Switch to "All Devices" to see discovered devices.'}
                </td>
              </tr>
            ) : (
              paginatedDevices.map((device) => (
                <DeviceRow
                  key={device.id}
                  device={device}
                  siteId={siteId}
                  expanded={expandedId === device.id}
                  onToggle={() => setExpandedId(expandedId === device.id ? null : device.id)}
                  onTakeOver={onTakeOver}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalDevFiltered > devPageSize && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-glass-border">
          <span className="text-sm text-label-tertiary">
            Showing {devStart}-{devEnd} of {totalDevFiltered}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setDevPage(p => Math.max(0, p - 1))}
              disabled={devPage === 0}
              className="px-3 py-1.5 text-sm rounded-lg bg-glass-bg text-label-secondary hover:bg-glass-bg/80 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Previous
            </button>
            <button
              onClick={() => setDevPage(p => Math.min(totalDevPages - 1, p + 1))}
              disabled={devPage >= totalDevPages - 1}
              className="px-3 py-1.5 text-sm rounded-lg bg-glass-bg text-label-secondary hover:bg-glass-bg/80 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Next
            </button>
          </div>
        </div>
      )}
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
  const [showAddNetworkDevice, setShowAddNetworkDevice] = useState(false);
  const [showAddMenu, setShowAddMenu] = useState(false);
  const [takeOverDevice, setTakeOverDevice] = useState<DiscoveredDevice | null>(null);
  const [showNeighbors, setShowNeighbors] = useState(false);

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

  const allDevices = devicesData?.devices || [];
  const devices = allDevices.filter(d => d.managed_network !== false);
  const neighborDevices = allDevices.filter(d => d.managed_network === false);

  return (
    <div className="space-y-6">
      {/* Org context banner */}
      {siteId && <OrgBanner siteId={siteId} />}

      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm">
        <Link to="/sites" className="text-label-secondary hover:text-label-primary">
          Sites
        </Link>
        <span className="text-label-tertiary">/</span>
        <Link to={`/sites/${siteId}`} className="text-label-secondary hover:text-label-primary">
          {siteId?.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || siteId}
        </Link>
        <span className="text-label-tertiary">/</span>
        <span className="text-label-primary">Devices</span>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-label-primary">Device Inventory</h1>
          <p className="text-label-secondary mt-1">
            {devices.length} device{devices.length !== 1 ? 's' : ''} on protected network
            {neighborDevices.length > 0 && (
              <span className="text-label-tertiary"> + {neighborDevices.length} on neighboring subnets</span>
            )}
          </p>
        </div>
        <div className="relative">
          <button
            onClick={() => setShowAddMenu(!showAddMenu)}
            onBlur={() => setTimeout(() => setShowAddMenu(false), 150)}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-accent-primary text-white hover:bg-accent-primary/90 transition flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Add Device
            <svg className="w-3 h-3 ml-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {showAddMenu && (
            <div className="absolute right-0 top-full mt-1 w-56 bg-background-secondary rounded-xl border border-separator-light shadow-lg z-40 overflow-hidden">
              <button
                onClick={() => { setShowAddMenu(false); setShowAddDevice(true); }}
                className="w-full px-4 py-3 text-left hover:bg-fill-secondary transition flex items-start gap-3"
              >
                <svg className="w-5 h-5 text-accent-primary mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-label-primary">Join Endpoint</p>
                  <p className="text-xs text-label-tertiary mt-0.5">Linux, macOS, or Windows via SSH</p>
                </div>
              </button>
              <div className="border-t border-separator-light" />
              <button
                onClick={() => { setShowAddMenu(false); setShowAddNetworkDevice(true); }}
                className="w-full px-4 py-3 text-left hover:bg-fill-secondary transition flex items-start gap-3"
              >
                <svg className="w-5 h-5 text-blue-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-label-primary">Add Network Device</p>
                  <p className="text-xs text-label-tertiary mt-0.5">Switch, router, AP, firewall (read-only)</p>
                </div>
              </button>
            </div>
          )}
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

      {/* Add Network Device Modal */}
      {showAddNetworkDevice && siteId && (
        <AddNetworkDeviceModal
          siteId={siteId}
          apiEndpoint={`/api/sites/${siteId}/devices/network`}
          onSuccess={() => {
            setShowAddNetworkDevice(false);
            window.location.reload();
          }}
          onClose={() => setShowAddNetworkDevice(false)}
        />
      )}

      {/* Summary */}
      {summary && <SummaryCard summary={summary} />}

      {/* Stale credential warning */}
      {summary && (summary.stale_credentials_count ?? 0) > 0 && (
        <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg px-4 py-2 mb-4 text-sm text-amber-400">
          {summary.stale_credentials_count} credential{(summary.stale_credentials_count ?? 0) > 1 ? 's' : ''} older than 90 days — consider rotating
        </div>
      )}

      {/* Protected network devices */}
      <DeviceTable
        devices={devices}
        siteId={siteId || ''}
        filter={filter}
        onFilterChange={setFilter}
        onTakeOver={setTakeOverDevice}
      />

      {/* Take Over modal */}
      {takeOverDevice && siteId && (
        <AddDeviceModal
          isOpen={!!takeOverDevice}
          siteId={siteId}
          apiEndpoint={`/api/sites/${siteId}/devices/takeover`}
          onSuccess={() => {
            setTakeOverDevice(null);
            window.location.reload();
          }}
          onClose={() => setTakeOverDevice(null)}
          prefill={{
            hostname: takeOverDevice.hostname || undefined,
            ip_address: takeOverDevice.ip_address,
            os_type: takeOverDevice.os_name || 'linux',
          }}
        />
      )}

      {/* Neighboring network devices (collapsed by default) */}
      {neighborDevices.length > 0 && (
        <GlassCard className="overflow-hidden">
          <button
            onClick={() => setShowNeighbors(!showNeighbors)}
            className="w-full px-4 py-3 flex items-center justify-between hover:bg-fill-quaternary transition-colors"
          >
            <div className="flex items-center gap-3">
              <svg
                className={`w-4 h-4 text-label-tertiary transition-transform ${showNeighbors ? 'rotate-90' : ''}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              <span className="text-sm font-medium text-label-secondary">
                Neighboring Network Devices
              </span>
              <Badge variant="default">{neighborDevices.length}</Badge>
            </div>
            <span className="text-xs text-label-tertiary">
              Discovered on adjacent subnets — not under active protection
            </span>
          </button>
          {showNeighbors && (
            <div className="border-t border-separator-light">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-glass-border">
                      <th className="px-4 py-2 text-left text-xs font-medium text-label-tertiary">Device</th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-label-tertiary">IP Address</th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-label-tertiary">MAC</th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-label-tertiary">Last Seen</th>
                    </tr>
                  </thead>
                  <tbody className="text-label-tertiary">
                    {neighborDevices.map((d) => (
                      <tr key={d.id} className="border-b border-glass-border/50">
                        <td className="px-4 py-2 text-sm">{d.hostname || d.ip_address}</td>
                        <td className="px-4 py-2 text-sm font-mono">{d.ip_address}</td>
                        <td className="px-4 py-2 text-sm font-mono">{d.mac_address || '-'}</td>
                        <td className="px-4 py-2 text-sm">{formatRelativeTime(d.last_seen_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </GlassCard>
      )}
    </div>
  );
};

export default SiteDevices;

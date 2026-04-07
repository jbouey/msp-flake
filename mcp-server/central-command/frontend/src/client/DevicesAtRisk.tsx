import React, { useState, useEffect } from 'react';
import { CATEGORY_LABELS, getScoreStatus } from '../constants';

// ─── Types ───────────────────────────────────────────────────────────────────

interface DeviceIncident {
  id: number;
  check_type: string;
  severity: string;
  resolution_level: string | null;
  created_at: string | null;
}

interface DeviceRisk {
  hostname: string;
  ip_address: string | null;
  device_type: string | null;
  os_name: string | null;
  active_incidents: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  health_score: number;
  worst_severity: string;
  categories: Record<string, number>;
  incidents: DeviceIncident[];
}

interface DevicesAtRiskData {
  site_id: string;
  total_devices_at_risk: number;
  devices: DeviceRisk[];
}

interface Props {
  siteId: string;
  apiPrefix?: string;  // '/api/client' or '/api/dashboard'
  onDeviceClick?: (hostname: string, siteId: string) => void;
}

// ─── Color Helpers ──────────────────────────────────────────────────────────

function severityColor(severity: string): string {
  switch (severity) {
    case 'critical': return '#FF3B30';
    case 'high': return '#FF9500';
    case 'medium': return '#FFD60A';
    case 'low': return '#34C759';
    default: return '#8E8E93';
  }
}

function healthColor(score: number): string {
  const status = getScoreStatus(score);
  const hexMap: Record<string, string> = {
    'text-health-healthy': '#34C759',
    'text-health-warning': '#FF9500',
    'text-ios-orange': '#FF9500',
    'text-health-critical': '#FF3B30',
    'text-label-tertiary': '#8E8E93',
  };
  return hexMap[status.color] || '#8E8E93';
}

const CHECK_TYPE_LABELS: Record<string, string> = {
  nixos_generation: 'NixOS Patching', windows_update: 'Windows Updates', linux_patching: 'Linux Patching',
  windows_defender: 'Windows Defender', windows_defender_exclusions: 'Defender Exclusions',
  backup_status: 'Backup Status', windows_backup_status: 'Windows Backup',
  audit_logging: 'Audit Logging', windows_audit_policy: 'Audit Policy', linux_audit: 'Linux Audit', linux_logging: 'Linux Logging',
  firewall: 'Firewall', windows_firewall_status: 'Windows Firewall', firewall_status: 'Firewall Status', linux_firewall: 'Linux Firewall',
  bitlocker: 'BitLocker', windows_bitlocker_status: 'BitLocker Status', linux_crypto: 'Linux Encryption', windows_smb_signing: 'SMB Signing',
  rogue_admin_users: 'Admin Users', linux_accounts: 'Linux Accounts', windows_password_policy: 'Password Policy',
  linux_permissions: 'Permissions', linux_ssh_config: 'SSH Config', windows_screen_lock_policy: 'Screen Lock',
  critical_services: 'Critical Services', linux_services: 'Linux Services',
  windows_service_dns: 'DNS Service', windows_service_netlogon: 'Netlogon', windows_service_spooler: 'Print Spooler',
  windows_service_w32time: 'Time Service', windows_service_wuauserv: 'Windows Update Service', agent_status: 'Agent Status',
};

// ─── Device Card ────────────────────────────────────────────────────────────

const DeviceCard: React.FC<{
  device: DeviceRisk;
  onClick?: () => void;
  expanded: boolean;
  onToggle: () => void;
}> = ({ device, onClick, expanded, onToggle }) => {
  const color = healthColor(device.health_score);
  const sevColor = severityColor(device.worst_severity);

  // Compute affected categories
  const affectedCats = Object.entries(device.categories)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);

  return (
    <div
      className="rounded-xl transition-all"
      style={{
        background: `linear-gradient(135deg, ${sevColor}06 0%, transparent 100%)`,
        border: `1px solid ${sevColor}20`,
      }}
    >
      {/* Main row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={onToggle}
        role="button"
        tabIndex={0}
      >
        {/* Health score circle */}
        <div className="relative flex-shrink-0">
          <svg width={40} height={40} viewBox="0 0 40 40">
            <circle cx={20} cy={20} r={16} fill="none" stroke="rgba(120,120,128,0.08)" strokeWidth={3} />
            <circle
              cx={20} cy={20} r={16}
              fill="none" stroke={color} strokeWidth={3} strokeLinecap="round"
              strokeDasharray={`${(device.health_score / 100) * 100.53} 100.53`}
              style={{ transform: 'rotate(-90deg)', transformOrigin: '20px 20px' }}
            />
          </svg>
          <span className="absolute inset-0 flex items-center justify-center text-xs font-bold tabular-nums" style={{ color }}>
            {device.health_score}
          </span>
        </div>

        {/* Device info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold truncate" style={{ color: 'var(--label-primary)' }}>
              {device.hostname}
            </span>
            {device.device_type && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{
                background: 'var(--fill-tertiary)',
                color: 'var(--label-tertiary)',
              }}>
                {device.device_type}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 mt-0.5">
            {device.ip_address && (
              <span className="text-xs font-mono" style={{ color: 'var(--label-tertiary)' }}>
                {device.ip_address}
              </span>
            )}
            {device.os_name && (
              <>
                <span className="text-xs" style={{ color: 'var(--separator-light)' }}>•</span>
                <span className="text-xs" style={{ color: 'var(--label-tertiary)' }}>{device.os_name}</span>
              </>
            )}
          </div>
        </div>

        {/* Severity badges */}
        <div className="flex items-center gap-1 flex-shrink-0">
          {device.critical_count > 0 && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold text-white" style={{ background: '#FF3B30' }}>
              {device.critical_count} CRIT
            </span>
          )}
          {device.high_count > 0 && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold text-white" style={{ background: '#FF9500' }}>
              {device.high_count} HIGH
            </span>
          )}
          {device.medium_count > 0 && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: '#FFD60A20', color: '#FFD60A' }}>
              {device.medium_count} MED
            </span>
          )}
          {device.low_count > 0 && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: '#34C75920', color: '#34C759' }}>
              {device.low_count} LOW
            </span>
          )}
        </div>

        {/* Expand chevron */}
        <svg
          className="w-4 h-4 flex-shrink-0 transition-transform duration-200"
          style={{
            color: 'var(--label-tertiary)',
            transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
          }}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-4 pb-3 pt-1 border-t" style={{ borderColor: `${sevColor}15` }}>
          {/* Category breakdown pills */}
          <div className="flex flex-wrap gap-1.5 mb-3">
            {affectedCats.map(([cat, count]) => (
              <span
                key={cat}
                className="px-2 py-1 rounded-lg text-[10px] font-medium"
                style={{ background: 'var(--fill-tertiary)', color: 'var(--label-secondary)' }}
              >
                {CATEGORY_LABELS[cat] || cat}: {count}
              </span>
            ))}
          </div>

          {/* Incident list */}
          <div className="space-y-1.5">
            {device.incidents.map(inc => (
              <div
                key={inc.id}
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs"
                style={{ background: 'var(--fill-quaternary)' }}
              >
                <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: severityColor(inc.severity) }} />
                <span className="flex-1 truncate" style={{ color: 'var(--label-primary)' }}>
                  {CHECK_TYPE_LABELS[inc.check_type] || inc.check_type}
                </span>
                {inc.resolution_level && (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium" style={{
                    background: inc.resolution_level === 'L1' ? '#34C75915' : inc.resolution_level === 'L2' ? '#14A89E15' : '#FF950015',
                    color: inc.resolution_level === 'L1' ? '#34C759' : inc.resolution_level === 'L2' ? '#14A89E' : '#FF9500',
                  }}>
                    {inc.resolution_level}
                  </span>
                )}
                {inc.created_at && (
                  <span style={{ color: 'var(--label-tertiary)' }}>
                    {new Date(inc.created_at).toLocaleDateString()}
                  </span>
                )}
              </div>
            ))}
          </div>

          {device.active_incidents > 5 && (
            <p className="text-[10px] mt-2 text-center" style={{ color: 'var(--label-tertiary)' }}>
              +{device.active_incidents - 5} more incidents
            </p>
          )}

          {/* View all link */}
          {onClick && (
            <button
              onClick={(e) => { e.stopPropagation(); onClick(); }}
              className="mt-2 w-full py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                background: 'rgba(20,168,158,0.08)',
                color: '#14A89E',
                border: '1px solid rgba(20,168,158,0.15)',
              }}
            >
              View all incidents for {device.hostname} →
            </button>
          )}
        </div>
      )}
    </div>
  );
};

// ─── Main Component ─────────────────────────────────────────────────────────

export const DevicesAtRisk: React.FC<Props> = ({ siteId, apiPrefix = '/api/client', onDeviceClick }) => {
  const [data, setData] = useState<DevicesAtRiskData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedDevice, setExpandedDevice] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!siteId) return;
    setLoading(true);
    fetch(`${apiPrefix}/sites/${siteId}/devices-at-risk`, {
      credentials: 'include',
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [siteId, apiPrefix]);

  if (loading) {
    return (
      <div className="mb-6 rounded-2xl overflow-hidden" style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--separator-light)',
        boxShadow: 'var(--card-shadow)',
      }}>
        <div className="px-6 py-8 flex items-center justify-center">
          <div className="w-6 h-6 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: 'var(--label-tertiary)', borderTopColor: 'transparent' }} />
          <span className="ml-3 text-sm" style={{ color: 'var(--label-tertiary)' }}>Analyzing device health...</span>
        </div>
      </div>
    );
  }

  if (!data || data.total_devices_at_risk === 0) {
    return (
      <div className="mb-6 rounded-2xl overflow-hidden" style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--separator-light)',
        boxShadow: 'var(--card-shadow)',
      }}>
        <div className="px-6 py-6 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'rgba(52,199,89,0.1)' }}>
            <svg className="w-5 h-5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <div>
            <h3 className="text-sm font-semibold" style={{ color: 'var(--label-primary)' }}>All Devices Passing</h3>
            <p className="text-xs" style={{ color: 'var(--label-tertiary)' }}>No active compliance issues detected</p>
          </div>
        </div>
      </div>
    );
  }

  const displayDevices = showAll ? data.devices : data.devices.slice(0, 5);
  const criticalDevices = data.devices.filter(d => d.worst_severity === 'critical').length;
  const highDevices = data.devices.filter(d => d.worst_severity === 'high').length;

  return (
    <div className="mb-6 rounded-2xl overflow-hidden" style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--separator-light)',
      boxShadow: 'var(--card-shadow)',
    }}>
      {/* Header */}
      <div className="px-6 pt-5 pb-3 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{
              background: criticalDevices > 0
                ? 'linear-gradient(135deg, rgba(255,59,48,0.15) 0%, rgba(255,149,0,0.1) 100%)'
                : 'linear-gradient(135deg, rgba(255,149,0,0.12) 0%, rgba(255,214,10,0.08) 100%)',
            }}
          >
            <svg className="w-5 h-5" style={{ color: criticalDevices > 0 ? '#FF3B30' : '#FF9500' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <div>
            <h2 className="text-base font-semibold" style={{ color: 'var(--label-primary)' }}>
              Devices at Risk
            </h2>
            <p className="text-xs" style={{ color: 'var(--label-tertiary)' }}>
              {data.total_devices_at_risk} device{data.total_devices_at_risk !== 1 ? 's' : ''} with active drift
              {criticalDevices > 0 && <span style={{ color: '#FF3B30' }}> • {criticalDevices} critical</span>}
              {highDevices > 0 && <span style={{ color: '#FF9500' }}> • {highDevices} high</span>}
            </p>
          </div>
        </div>
      </div>

      {/* Device list */}
      <div className="px-6 pb-5 space-y-2">
        {displayDevices.map(device => (
          <DeviceCard
            key={device.hostname}
            device={device}
            expanded={expandedDevice === device.hostname}
            onToggle={() => setExpandedDevice(expandedDevice === device.hostname ? null : device.hostname)}
            onClick={onDeviceClick ? () => onDeviceClick(device.hostname, siteId) : undefined}
          />
        ))}

        {/* Show more/less toggle */}
        {data.devices.length > 5 && (
          <button
            onClick={() => setShowAll(!showAll)}
            className="w-full py-2 rounded-xl text-xs font-medium transition-colors"
            style={{
              background: 'var(--fill-quaternary)',
              color: 'var(--label-secondary)',
            }}
          >
            {showAll ? 'Show fewer devices' : `Show all ${data.devices.length} devices`}
          </button>
        )}
      </div>
    </div>
  );
};

export default DevicesAtRisk;

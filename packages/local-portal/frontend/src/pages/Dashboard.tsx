import {
  Monitor,
  Shield,
  AlertTriangle,
  Activity,
  Stethoscope,
  RefreshCw,
} from 'lucide-react'
import { GlassCard } from '../components/GlassCard'
import { KPICard } from '../components/KPICard'
import { ComplianceBadge, DeviceTypeBadge } from '../components/Badge'
import { useDashboard, useTriggerScan, useLatestScan } from '../hooks/useApi'

export function Dashboard() {
  const { data, isLoading, error } = useDashboard()
  const { data: latestScanData } = useLatestScan()
  const triggerScan = useTriggerScan()

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="h-8 w-8 animate-spin text-accent-primary" />
      </div>
    )
  }

  if (error) {
    return (
      <GlassCard className="text-center py-8">
        <AlertTriangle className="h-12 w-12 text-health-warning mx-auto mb-4" />
        <p className="text-label-secondary">Failed to load dashboard data</p>
        <p className="text-sm text-label-tertiary mt-2">{(error as Error).message}</p>
      </GlassCard>
    )
  }

  const { devices, compliance, device_types } = data!
  const latestScan = latestScanData?.scan || data!.last_scan

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-label-primary">Dashboard</h1>
          <p className="text-label-secondary">{data?.site_name || 'Local Site'}</p>
        </div>
        <button
          onClick={() => triggerScan.mutate('full')}
          disabled={triggerScan.isPending}
          className="btn-primary flex items-center gap-2"
        >
          <RefreshCw className={`h-4 w-4 ${triggerScan.isPending ? 'animate-spin' : ''}`} />
          {triggerScan.isPending ? 'Scanning...' : 'Trigger Scan'}
        </button>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KPICard
          value={devices.total}
          label="Total Devices"
          icon={<Monitor className="h-6 w-6" />}
        />
        <KPICard
          value={`${compliance.compliance_rate}%`}
          label="Compliance Rate"
          icon={<Shield className="h-6 w-6" />}
          variant={compliance.compliance_rate >= 90 ? 'success' : compliance.compliance_rate >= 70 ? 'warning' : 'error'}
        />
        <KPICard
          value={compliance.drifted}
          label="Needs Attention"
          icon={<AlertTriangle className="h-6 w-6" />}
          variant={compliance.drifted > 0 ? 'warning' : 'success'}
        />
        <KPICard
          value={devices.medical}
          label="Medical Excluded"
          icon={<Stethoscope className="h-6 w-6" />}
          variant={devices.medical > 0 ? 'error' : 'default'}
        />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        {/* Device Breakdown */}
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Monitor className="h-5 w-5 text-accent-primary" />
            Device Breakdown
          </h2>
          <div className="space-y-3">
            {device_types.map(({ device_type, count }) => (
              <div key={device_type} className="flex items-center justify-between">
                <DeviceTypeBadge type={device_type as any} />
                <span className="text-label-primary font-medium">{count}</span>
              </div>
            ))}
            {device_types.length === 0 && (
              <p className="text-label-tertiary text-center py-4">No devices discovered yet</p>
            )}
          </div>
        </GlassCard>

        {/* Compliance Summary */}
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Shield className="h-5 w-5 text-accent-primary" />
            Compliance Summary
          </h2>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <ComplianceBadge status="compliant" />
              <span className="text-label-primary font-medium">{compliance.compliant}</span>
            </div>
            <div className="flex items-center justify-between">
              <ComplianceBadge status="drifted" />
              <span className="text-label-primary font-medium">{compliance.drifted}</span>
            </div>
            <div className="flex items-center justify-between">
              <ComplianceBadge status="unknown" />
              <span className="text-label-primary font-medium">{compliance.unknown}</span>
            </div>
            <div className="flex items-center justify-between">
              <ComplianceBadge status="excluded" />
              <span className="text-label-primary font-medium">{compliance.excluded}</span>
            </div>
          </div>
        </GlassCard>
      </div>

      {/* Last Scan Info */}
      {latestScan && (
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Activity className="h-5 w-5 text-accent-primary" />
            Last Network Scan
          </h2>
          <div className="grid md:grid-cols-4 gap-4 text-sm">
            <div>
              <p className="text-label-tertiary">Status</p>
              <p className="text-label-primary font-medium capitalize">{latestScan.status}</p>
            </div>
            <div>
              <p className="text-label-tertiary">Started</p>
              <p className="text-label-primary font-medium">
                {new Date(latestScan.started_at).toLocaleString()}
              </p>
            </div>
            <div>
              <p className="text-label-tertiary">Devices Found</p>
              <p className="text-label-primary font-medium">{latestScan.devices_found}</p>
            </div>
            <div>
              <p className="text-label-tertiary">New Devices</p>
              <p className="text-label-primary font-medium">{latestScan.new_devices}</p>
            </div>
          </div>
        </GlassCard>
      )}
    </div>
  )
}

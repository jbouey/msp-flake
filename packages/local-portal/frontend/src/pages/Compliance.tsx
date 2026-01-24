import { Link } from 'react-router-dom'
import {
  Shield,
  AlertTriangle,
  CheckCircle,
  XCircle,
  RefreshCw,
  ChevronRight,
} from 'lucide-react'
import { GlassCard } from '../components/GlassCard'
import { KPICard } from '../components/KPICard'
import { ComplianceBadge, DeviceTypeBadge } from '../components/Badge'
import { useComplianceSummary, useDriftedDevices } from '../hooks/useApi'

export function Compliance() {
  const { data: summary, isLoading: summaryLoading } = useComplianceSummary()
  const { data: drifted, isLoading: driftedLoading } = useDriftedDevices()

  const isLoading = summaryLoading || driftedLoading

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="h-8 w-8 animate-spin text-accent-primary" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-label-primary">Compliance Overview</h1>
        <p className="text-label-secondary">HIPAA compliance status for your network</p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KPICard
          value={`${summary?.compliance.rate || 0}%`}
          label="Compliance Rate"
          icon={<Shield className="h-6 w-6" />}
          variant={
            (summary?.compliance.rate || 0) >= 90 ? 'success' :
            (summary?.compliance.rate || 0) >= 70 ? 'warning' : 'error'
          }
        />
        <KPICard
          value={summary?.compliance.compliant || 0}
          label="Compliant"
          icon={<CheckCircle className="h-6 w-6" />}
          variant="success"
        />
        <KPICard
          value={summary?.compliance.drifted || 0}
          label="Drifted"
          icon={<AlertTriangle className="h-6 w-6" />}
          variant={summary?.compliance.drifted ? 'warning' : 'default'}
        />
        <KPICard
          value={summary?.medical_excluded || 0}
          label="Medical Excluded"
          icon={<XCircle className="h-6 w-6" />}
        />
      </div>

      {/* Compliance Breakdown */}
      <GlassCard>
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Shield className="h-5 w-5 text-accent-primary" />
          Status Breakdown
        </h2>
        <div className="grid md:grid-cols-4 gap-4">
          <div className="text-center p-4 bg-green-50 rounded-ios-md">
            <div className="text-3xl font-bold text-health-healthy">
              {summary?.compliance.compliant || 0}
            </div>
            <div className="text-sm text-label-secondary mt-1">Compliant</div>
          </div>
          <div className="text-center p-4 bg-orange-50 rounded-ios-md">
            <div className="text-3xl font-bold text-health-warning">
              {summary?.compliance.drifted || 0}
            </div>
            <div className="text-sm text-label-secondary mt-1">Drifted</div>
          </div>
          <div className="text-center p-4 bg-gray-50 rounded-ios-md">
            <div className="text-3xl font-bold text-label-tertiary">
              {summary?.compliance.unknown || 0}
            </div>
            <div className="text-sm text-label-secondary mt-1">Unknown</div>
          </div>
          <div className="text-center p-4 bg-purple-50 rounded-ios-md">
            <div className="text-3xl font-bold text-ios-purple">
              {summary?.compliance.excluded || 0}
            </div>
            <div className="text-sm text-label-secondary mt-1">Excluded</div>
          </div>
        </div>
      </GlassCard>

      {/* Drifted Devices */}
      <GlassCard>
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-health-warning" />
          Devices Requiring Attention
        </h2>
        {drifted?.devices && drifted.devices.length > 0 ? (
          <div className="space-y-2">
            {drifted.devices.map((device) => (
              <Link
                key={device.id}
                to={`/devices/${device.id}`}
                className="flex items-center justify-between px-4 py-3 bg-orange-50 rounded-ios-sm hover:bg-orange-100 transition-colors"
              >
                <div className="flex items-center gap-4">
                  <div>
                    <div className="font-medium text-label-primary">
                      {device.hostname || 'Unknown Host'}
                    </div>
                    <div className="text-sm text-label-secondary">{device.ip_address}</div>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <DeviceTypeBadge type={device.device_type as any} />
                  <ComplianceBadge status="drifted" />
                  <ChevronRight className="h-5 w-5 text-label-tertiary" />
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <div className="text-center py-8">
            <CheckCircle className="h-12 w-12 text-health-healthy mx-auto mb-4" />
            <p className="text-label-secondary">All devices are compliant!</p>
          </div>
        )}
      </GlassCard>
    </div>
  )
}

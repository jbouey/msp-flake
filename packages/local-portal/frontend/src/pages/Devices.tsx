import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Monitor, Filter, RefreshCw, ChevronRight } from 'lucide-react'
import { GlassCard } from '../components/GlassCard'
import { ComplianceBadge, DeviceTypeBadge, DeviceStatusBadge } from '../components/Badge'
import { useDevices } from '../hooks/useApi'

const deviceTypes = ['', 'workstation', 'server', 'network', 'printer', 'medical', 'unknown']
const statuses = ['', 'discovered', 'monitored', 'excluded', 'offline']
const complianceStatuses = ['', 'compliant', 'drifted', 'unknown', 'excluded']

export function Devices() {
  const [filters, setFilters] = useState({
    device_type: '',
    status: '',
    compliance_status: '',
    page: 1,
  })

  const { data, isLoading, error, refetch } = useDevices({
    ...filters,
    device_type: filters.device_type || undefined,
    status: filters.status || undefined,
    compliance_status: filters.compliance_status || undefined,
  })

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-label-primary">Device Inventory</h1>
          <p className="text-label-secondary">{data?.total || 0} devices total</p>
        </div>
        <button
          onClick={() => refetch()}
          className="btn-secondary flex items-center gap-2"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <GlassCard padding="md">
        <div className="flex items-center gap-4 flex-wrap">
          <Filter className="h-5 w-5 text-label-tertiary" />
          <select
            value={filters.device_type}
            onChange={(e) => setFilters({ ...filters, device_type: e.target.value, page: 1 })}
            className="px-3 py-1.5 rounded-ios-sm border border-separator-medium bg-white text-sm"
          >
            <option value="">All Types</option>
            {deviceTypes.filter(Boolean).map((type) => (
              <option key={type} value={type}>
                {type.charAt(0).toUpperCase() + type.slice(1)}
              </option>
            ))}
          </select>
          <select
            value={filters.status}
            onChange={(e) => setFilters({ ...filters, status: e.target.value, page: 1 })}
            className="px-3 py-1.5 rounded-ios-sm border border-separator-medium bg-white text-sm"
          >
            <option value="">All Statuses</option>
            {statuses.filter(Boolean).map((status) => (
              <option key={status} value={status}>
                {status.charAt(0).toUpperCase() + status.slice(1)}
              </option>
            ))}
          </select>
          <select
            value={filters.compliance_status}
            onChange={(e) => setFilters({ ...filters, compliance_status: e.target.value, page: 1 })}
            className="px-3 py-1.5 rounded-ios-sm border border-separator-medium bg-white text-sm"
          >
            <option value="">All Compliance</option>
            {complianceStatuses.filter(Boolean).map((status) => (
              <option key={status} value={status}>
                {status.charAt(0).toUpperCase() + status.slice(1)}
              </option>
            ))}
          </select>
        </div>
      </GlassCard>

      {/* Device List */}
      {isLoading ? (
        <div className="flex items-center justify-center h-64">
          <RefreshCw className="h-8 w-8 animate-spin text-accent-primary" />
        </div>
      ) : error ? (
        <GlassCard className="text-center py-8">
          <p className="text-health-warning">Failed to load devices</p>
        </GlassCard>
      ) : (
        <div className="space-y-2">
          {data?.devices.map((device) => (
            <Link
              key={device.id}
              to={`/devices/${device.id}`}
              className="block"
            >
              <GlassCard
                hover
                padding="md"
                className="flex items-center justify-between"
              >
                <div className="flex items-center gap-4">
                  <Monitor className="h-8 w-8 text-label-tertiary" />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-label-primary">
                        {device.hostname || 'Unknown Host'}
                      </span>
                      {device.medical_device === 1 && (
                        <span className="text-xs text-health-critical font-medium">
                          MEDICAL
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-label-secondary">{device.ip_address}</p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="flex items-center gap-2">
                    <DeviceTypeBadge type={device.device_type as any} />
                    <DeviceStatusBadge status={device.status as any} />
                    <ComplianceBadge status={device.compliance_status as any} />
                  </div>
                  <ChevronRight className="h-5 w-5 text-label-tertiary" />
                </div>
              </GlassCard>
            </Link>
          ))}
          {data?.devices.length === 0 && (
            <GlassCard className="text-center py-8">
              <Monitor className="h-12 w-12 text-label-tertiary mx-auto mb-4" />
              <p className="text-label-secondary">No devices found</p>
            </GlassCard>
          )}
        </div>
      )}
    </div>
  )
}

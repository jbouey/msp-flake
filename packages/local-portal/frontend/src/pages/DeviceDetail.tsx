import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeft,
  Monitor,
  Network,
  Shield,
  Clock,
  AlertTriangle,
  Stethoscope,
  RefreshCw,
} from 'lucide-react'
import { GlassCard } from '../components/GlassCard'
import { ComplianceBadge, DeviceTypeBadge, DeviceStatusBadge, Badge } from '../components/Badge'
import { useDevice, useUpdateDevicePolicy } from '../hooks/useApi'

export function DeviceDetail() {
  const { deviceId } = useParams<{ deviceId: string }>()
  const { data, isLoading, error } = useDevice(deviceId!)
  const updatePolicy = useUpdateDevicePolicy()
  const [showOptIn, setShowOptIn] = useState(false)
  const [optInReason, setOptInReason] = useState('')

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="h-8 w-8 animate-spin text-accent-primary" />
      </div>
    )
  }

  if (error || !data) {
    return (
      <GlassCard className="text-center py-8">
        <AlertTriangle className="h-12 w-12 text-health-warning mx-auto mb-4" />
        <p className="text-label-secondary">Device not found</p>
        <Link to="/devices" className="btn-primary inline-block mt-4">
          Back to Devices
        </Link>
      </GlassCard>
    )
  }

  const { device, ports, compliance_checks, notes } = data
  const isMedical = device.medical_device === 1

  const handleOptIn = async () => {
    await updatePolicy.mutateAsync({
      deviceId: device.id,
      policy: {
        scan_policy: 'limited',
        manually_opted_in: true,
        reason: optInReason,
      },
    })
    setShowOptIn(false)
    setOptInReason('')
  }

  const handleExclude = async () => {
    await updatePolicy.mutateAsync({
      deviceId: device.id,
      policy: {
        scan_policy: 'excluded',
      },
    })
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to="/devices" className="p-2 hover:bg-gray-100 rounded-ios-sm">
          <ArrowLeft className="h-5 w-5 text-label-secondary" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-label-primary flex items-center gap-2">
            {device.hostname || 'Unknown Host'}
            {isMedical && (
              <Badge variant="error">
                <Stethoscope className="h-3 w-3 mr-1" />
                Medical Device
              </Badge>
            )}
          </h1>
          <p className="text-label-secondary">{device.ip_address}</p>
        </div>
        <div className="flex items-center gap-2">
          <DeviceTypeBadge type={device.device_type as any} />
          <DeviceStatusBadge status={device.status as any} />
          <ComplianceBadge status={device.compliance_status as any} />
        </div>
      </div>

      {/* Medical Device Warning */}
      {isMedical && device.scan_policy === 'excluded' && (
        <GlassCard className="border-2 border-health-critical/30 bg-red-50/50">
          <div className="flex items-start gap-4">
            <Stethoscope className="h-8 w-8 text-health-critical flex-shrink-0" />
            <div className="flex-1">
              <h3 className="font-semibold text-health-critical">Medical Device - Excluded from Scanning</h3>
              <p className="text-sm text-label-secondary mt-1">
                This device has been identified as medical equipment and is excluded from compliance
                scanning by default for patient safety. Medical devices require explicit opt-in to
                enable limited scanning.
              </p>
              {!showOptIn ? (
                <button
                  onClick={() => setShowOptIn(true)}
                  className="btn-secondary mt-3 text-sm"
                >
                  Opt-In for Limited Scanning
                </button>
              ) : (
                <div className="mt-3 space-y-3">
                  <textarea
                    value={optInReason}
                    onChange={(e) => setOptInReason(e.target.value)}
                    placeholder="Reason for opt-in (required for audit trail)..."
                    className="w-full p-3 border border-separator-medium rounded-ios-sm text-sm"
                    rows={2}
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={handleOptIn}
                      disabled={!optInReason.trim() || updatePolicy.isPending}
                      className="btn-primary text-sm"
                    >
                      {updatePolicy.isPending ? 'Processing...' : 'Confirm Opt-In'}
                    </button>
                    <button
                      onClick={() => setShowOptIn(false)}
                      className="btn-secondary text-sm"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </GlassCard>
      )}

      <div className="grid md:grid-cols-2 gap-6">
        {/* Device Info */}
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Monitor className="h-5 w-5 text-accent-primary" />
            Device Information
          </h2>
          <dl className="space-y-3 text-sm">
            <div className="flex justify-between">
              <dt className="text-label-tertiary">Hostname</dt>
              <dd className="text-label-primary font-medium">{device.hostname || 'Unknown'}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-label-tertiary">IP Address</dt>
              <dd className="text-label-primary font-mono">{device.ip_address}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-label-tertiary">MAC Address</dt>
              <dd className="text-label-primary font-mono">{device.mac_address || 'Unknown'}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-label-tertiary">OS</dt>
              <dd className="text-label-primary">
                {device.os_name ? `${device.os_name} ${device.os_version || ''}` : 'Unknown'}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-label-tertiary">Scan Policy</dt>
              <dd className="text-label-primary capitalize">{device.scan_policy}</dd>
            </div>
          </dl>
        </GlassCard>

        {/* Timeline */}
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Clock className="h-5 w-5 text-accent-primary" />
            Timeline
          </h2>
          <dl className="space-y-3 text-sm">
            <div className="flex justify-between">
              <dt className="text-label-tertiary">First Seen</dt>
              <dd className="text-label-primary">
                {new Date(device.first_seen_at).toLocaleDateString()}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-label-tertiary">Last Seen</dt>
              <dd className="text-label-primary">
                {new Date(device.last_seen_at).toLocaleDateString()}
              </dd>
            </div>
            {device.last_scan_at && (
              <div className="flex justify-between">
                <dt className="text-label-tertiary">Last Scanned</dt>
                <dd className="text-label-primary">
                  {new Date(device.last_scan_at).toLocaleDateString()}
                </dd>
              </div>
            )}
          </dl>
        </GlassCard>
      </div>

      {/* Ports */}
      {ports.length > 0 && (
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Network className="h-5 w-5 text-accent-primary" />
            Open Ports
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {ports.map((port) => (
              <div
                key={`${port.port}-${port.protocol}`}
                className="px-3 py-2 bg-background-primary rounded-ios-sm text-sm"
              >
                <span className="font-mono font-medium">{port.port}</span>
                <span className="text-label-tertiary">/{port.protocol}</span>
                {port.service_name && (
                  <span className="text-label-secondary ml-2">({port.service_name})</span>
                )}
              </div>
            ))}
          </div>
        </GlassCard>
      )}

      {/* Compliance Checks */}
      {compliance_checks.length > 0 && (
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Shield className="h-5 w-5 text-accent-primary" />
            Compliance Checks
          </h2>
          <div className="space-y-2">
            {compliance_checks.map((check, i) => (
              <div
                key={i}
                className="flex items-center justify-between px-3 py-2 bg-background-primary rounded-ios-sm"
              >
                <div>
                  <span className="font-medium text-label-primary">{check.check_type}</span>
                  {check.hipaa_control && (
                    <span className="text-xs text-label-tertiary ml-2">
                      HIPAA {check.hipaa_control}
                    </span>
                  )}
                </div>
                <Badge
                  variant={
                    check.status === 'pass' ? 'success' :
                    check.status === 'fail' ? 'error' :
                    check.status === 'warn' ? 'warning' : 'default'
                  }
                >
                  {check.status.toUpperCase()}
                </Badge>
              </div>
            ))}
          </div>
        </GlassCard>
      )}

      {/* Notes */}
      {notes.length > 0 && (
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4">Notes</h2>
          <div className="space-y-3">
            {notes.map((note, i) => (
              <div key={i} className="px-3 py-2 bg-background-primary rounded-ios-sm">
                <p className="text-sm text-label-primary">{note.note}</p>
                <p className="text-xs text-label-tertiary mt-1">
                  {note.created_by} - {new Date(note.created_at).toLocaleString()}
                </p>
              </div>
            ))}
          </div>
        </GlassCard>
      )}

      {/* Actions */}
      {!isMedical && device.scan_policy !== 'excluded' && (
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4">Actions</h2>
          <button
            onClick={handleExclude}
            disabled={updatePolicy.isPending}
            className="btn-secondary text-sm"
          >
            Exclude from Scanning
          </button>
        </GlassCard>
      )}
    </div>
  )
}

import { ReactNode } from 'react'

type ComplianceStatus = 'compliant' | 'drifted' | 'unknown' | 'excluded'
type DeviceType = 'workstation' | 'server' | 'network' | 'printer' | 'medical' | 'unknown'
type DeviceStatus = 'discovered' | 'monitored' | 'excluded' | 'offline'

interface BadgeProps {
  children: ReactNode
  variant?: 'default' | 'success' | 'warning' | 'error' | 'info' | 'purple'
  className?: string
}

const variantClasses = {
  default: 'bg-gray-100 text-label-secondary',
  success: 'bg-green-100 text-health-healthy',
  warning: 'bg-orange-100 text-health-warning',
  error: 'bg-red-100 text-health-critical',
  info: 'bg-blue-100 text-ios-blue',
  purple: 'bg-purple-100 text-ios-purple',
}

export function Badge({ children, variant = 'default', className = '' }: BadgeProps) {
  return (
    <span className={`status-badge ${variantClasses[variant]} ${className}`}>
      {children}
    </span>
  )
}

// Specialized badges
export function ComplianceBadge({ status }: { status: ComplianceStatus }) {
  const config: Record<ComplianceStatus, { variant: BadgeProps['variant']; label: string }> = {
    compliant: { variant: 'success', label: 'Compliant' },
    drifted: { variant: 'warning', label: 'Drifted' },
    unknown: { variant: 'default', label: 'Unknown' },
    excluded: { variant: 'purple', label: 'Excluded' },
  }

  const { variant, label } = config[status] || config.unknown
  return <Badge variant={variant}>{label}</Badge>
}

export function DeviceTypeBadge({ type }: { type: DeviceType }) {
  const config: Record<DeviceType, { variant: BadgeProps['variant']; label: string }> = {
    workstation: { variant: 'info', label: 'Workstation' },
    server: { variant: 'purple', label: 'Server' },
    network: { variant: 'info', label: 'Network' },
    printer: { variant: 'default', label: 'Printer' },
    medical: { variant: 'error', label: 'Medical' },
    unknown: { variant: 'default', label: 'Unknown' },
  }

  const { variant, label } = config[type] || config.unknown
  return <Badge variant={variant}>{label}</Badge>
}

export function DeviceStatusBadge({ status }: { status: DeviceStatus }) {
  const config: Record<DeviceStatus, { variant: BadgeProps['variant']; label: string }> = {
    discovered: { variant: 'info', label: 'Discovered' },
    monitored: { variant: 'success', label: 'Monitored' },
    excluded: { variant: 'purple', label: 'Excluded' },
    offline: { variant: 'default', label: 'Offline' },
  }

  const { variant, label } = config[status] || config.discovered
  return <Badge variant={variant}>{label}</Badge>
}

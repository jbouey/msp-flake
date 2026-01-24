import { ReactNode } from 'react'
import { GlassCard } from './GlassCard'

interface KPICardProps {
  value: number | string
  label: string
  icon?: ReactNode
  trend?: 'up' | 'down' | 'neutral'
  trendValue?: string
  variant?: 'default' | 'success' | 'warning' | 'error'
}

const variantColors = {
  default: 'text-label-primary',
  success: 'text-health-healthy',
  warning: 'text-health-warning',
  error: 'text-health-critical',
}

export function KPICard({
  value,
  label,
  icon,
  variant = 'default',
}: KPICardProps) {
  return (
    <GlassCard padding="md" className="flex flex-col items-center text-center min-h-[120px] justify-center">
      {icon && <div className="mb-2 text-label-tertiary">{icon}</div>}
      <div className={`kpi-value ${variantColors[variant]}`}>{value}</div>
      <div className="kpi-label">{label}</div>
    </GlassCard>
  )
}

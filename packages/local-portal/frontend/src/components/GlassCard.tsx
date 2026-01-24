import { ReactNode } from 'react'

interface GlassCardProps {
  children: ReactNode
  className?: string
  hover?: boolean
  onClick?: () => void
  padding?: 'none' | 'sm' | 'md' | 'lg'
}

const paddingClasses = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
}

export function GlassCard({
  children,
  className = '',
  hover = false,
  onClick,
  padding = 'lg',
}: GlassCardProps) {
  const baseClasses = 'glass-card'
  const hoverClasses = hover ? 'cursor-pointer hover:shadow-card-hover hover:scale-[1.01]' : ''
  const paddingClass = paddingClasses[padding]

  return (
    <div
      onClick={onClick}
      className={`${baseClasses} ${hoverClasses} ${paddingClass} ${className}`}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => e.key === 'Enter' && onClick() : undefined}
    >
      {children}
    </div>
  )
}

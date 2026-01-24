import { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Monitor,
  Shield,
  Download,
  Server
} from 'lucide-react'

interface LayoutProps {
  children: ReactNode
}

const navItems = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/devices', label: 'Devices', icon: Monitor },
  { path: '/compliance', label: 'Compliance', icon: Shield },
  { path: '/exports', label: 'Exports', icon: Download },
]

export function Layout({ children }: LayoutProps) {
  const location = useLocation()

  return (
    <div className="min-h-screen bg-background-primary">
      {/* Header */}
      <header className="bg-background-secondary border-b border-separator-light sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-3">
              <Server className="h-8 w-8 text-accent-primary" />
              <div>
                <h1 className="text-lg font-semibold text-label-primary">Local Portal</h1>
                <p className="text-xs text-label-tertiary">MSP Compliance Appliance</p>
              </div>
            </div>

            <nav className="flex items-center gap-1">
              {navItems.map(({ path, label, icon: Icon }) => {
                const isActive = location.pathname === path ||
                  (path !== '/' && location.pathname.startsWith(path))
                return (
                  <Link
                    key={path}
                    to={path}
                    className={`
                      flex items-center gap-2 px-4 py-2 rounded-ios-sm text-sm font-medium
                      transition-colors
                      ${isActive
                        ? 'bg-accent-tint text-accent-primary'
                        : 'text-label-secondary hover:text-label-primary hover:bg-gray-100'
                      }
                    `}
                  >
                    <Icon className="h-4 w-4" />
                    <span className="hidden sm:inline">{label}</span>
                  </Link>
                )
              })}
            </nav>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {children}
      </main>

      {/* Footer */}
      <footer className="border-t border-separator-light py-4 mt-auto">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <p className="text-xs text-label-tertiary text-center">
            MSP Compliance Platform - Local Portal v0.1.0
          </p>
        </div>
      </footer>
    </div>
  )
}

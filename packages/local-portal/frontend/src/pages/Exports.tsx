import { useState } from 'react'
import { Download, FileText, FileSpreadsheet, RefreshCw } from 'lucide-react'
import { GlassCard } from '../components/GlassCard'

interface ExportOption {
  id: string
  title: string
  description: string
  icon: React.ReactNode
  endpoint: string
  filename: string
}

const exportOptions: ExportOption[] = [
  {
    id: 'devices-csv',
    title: 'Device Inventory (CSV)',
    description: 'Export all discovered devices with details in spreadsheet format',
    icon: <FileSpreadsheet className="h-8 w-8 text-health-healthy" />,
    endpoint: '/api/exports/csv/devices',
    filename: 'device_inventory.csv',
  },
  {
    id: 'compliance-csv',
    title: 'Compliance Report (CSV)',
    description: 'Export compliance status for all devices in spreadsheet format',
    icon: <FileSpreadsheet className="h-8 w-8 text-ios-blue" />,
    endpoint: '/api/exports/csv/compliance',
    filename: 'compliance_report.csv',
  },
  {
    id: 'compliance-pdf',
    title: 'Compliance Report (PDF)',
    description: 'Generate a formatted compliance report for auditors',
    icon: <FileText className="h-8 w-8 text-health-critical" />,
    endpoint: '/api/exports/pdf/compliance',
    filename: 'compliance_report.pdf',
  },
  {
    id: 'inventory-pdf',
    title: 'Device Inventory (PDF)',
    description: 'Generate a formatted device inventory document',
    icon: <FileText className="h-8 w-8 text-ios-orange" />,
    endpoint: '/api/exports/pdf/inventory',
    filename: 'device_inventory.pdf',
  },
]

export function Exports() {
  const [downloading, setDownloading] = useState<string | null>(null)

  const handleDownload = async (option: ExportOption) => {
    setDownloading(option.id)
    try {
      const response = await fetch(option.endpoint)
      if (!response.ok) {
        throw new Error('Export failed')
      }

      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = option.filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (error) {
      console.error('Download failed:', error)
      alert('Export failed. Please try again.')
    } finally {
      setDownloading(null)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-label-primary">Export Reports</h1>
        <p className="text-label-secondary">Download inventory and compliance reports</p>
      </div>

      {/* Export Options */}
      <div className="grid md:grid-cols-2 gap-4">
        {exportOptions.map((option) => (
          <GlassCard
            key={option.id}
            hover
            onClick={() => handleDownload(option)}
            className={downloading === option.id ? 'opacity-50' : ''}
          >
            <div className="flex items-start gap-4">
              {option.icon}
              <div className="flex-1">
                <h3 className="font-semibold text-label-primary">{option.title}</h3>
                <p className="text-sm text-label-secondary mt-1">{option.description}</p>
              </div>
              <div className="flex-shrink-0">
                {downloading === option.id ? (
                  <RefreshCw className="h-5 w-5 animate-spin text-accent-primary" />
                ) : (
                  <Download className="h-5 w-5 text-label-tertiary" />
                )}
              </div>
            </div>
          </GlassCard>
        ))}
      </div>

      {/* Info */}
      <GlassCard className="bg-blue-50/50 border border-ios-blue/20">
        <div className="flex items-start gap-3">
          <FileText className="h-6 w-6 text-ios-blue flex-shrink-0" />
          <div className="text-sm">
            <p className="font-medium text-label-primary">About Reports</p>
            <p className="text-label-secondary mt-1">
              CSV files can be opened in Excel or Google Sheets for further analysis.
              PDF reports are formatted for printing and sharing with auditors.
              All reports include timestamps and are generated from real-time data.
            </p>
          </div>
        </div>
      </GlassCard>

      {/* Medical Device Notice */}
      <GlassCard className="bg-purple-50/50 border border-ios-purple/20">
        <div className="flex items-start gap-3">
          <FileText className="h-6 w-6 text-ios-purple flex-shrink-0" />
          <div className="text-sm">
            <p className="font-medium text-label-primary">Medical Device Handling</p>
            <p className="text-label-secondary mt-1">
              Medical devices are excluded from compliance scanning by default and are
              marked separately in all reports. This ensures patient safety while maintaining
              complete network visibility.
            </p>
          </div>
        </div>
      </GlassCard>
    </div>
  )
}

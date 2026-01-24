const API_BASE = '/api'

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }

  return response.json()
}

// Dashboard API
export interface DashboardData {
  site_name: string
  appliance_id: string | null
  devices: {
    total: number
    monitored: number
    discovered: number
    excluded: number
    offline: number
    medical: number
  }
  compliance: {
    total_assessed: number
    compliant: number
    drifted: number
    unknown: number
    excluded: number
    compliance_rate: number
  }
  device_types: Array<{ device_type: string; count: number }>
  last_scan: {
    scan_id: string
    started_at: string
    completed_at: string | null
    devices_found: number
    new_devices: number
    status: string
  } | null
  generated_at: string
}

export const getDashboard = () => fetchJson<DashboardData>('/dashboard')

// Devices API
export interface Device {
  id: string
  hostname: string | null
  ip_address: string
  mac_address: string | null
  device_type: string
  os_name: string | null
  os_version: string | null
  status: string
  compliance_status: string
  medical_device: number
  scan_policy: string
  manually_opted_in: number
  first_seen_at: string
  last_seen_at: string
  last_scan_at: string | null
}

export interface DeviceListResponse {
  devices: Device[]
  total: number
  page: number
  page_size: number
}

export interface DeviceDetailResponse {
  device: Device
  ports: Array<{
    port: number
    protocol: string
    service_name: string | null
    state: string
  }>
  compliance_checks: Array<{
    check_type: string
    hipaa_control: string | null
    status: string
    checked_at: string
  }>
  notes: Array<{
    note: string
    created_by: string
    created_at: string
  }>
}

export const getDevices = (params?: {
  device_type?: string
  status?: string
  compliance_status?: string
  page?: number
  page_size?: number
}) => {
  const searchParams = new URLSearchParams()
  if (params?.device_type) searchParams.set('device_type', params.device_type)
  if (params?.status) searchParams.set('status', params.status)
  if (params?.compliance_status) searchParams.set('compliance_status', params.compliance_status)
  if (params?.page) searchParams.set('page', params.page.toString())
  if (params?.page_size) searchParams.set('page_size', params.page_size.toString())
  const query = searchParams.toString()
  return fetchJson<DeviceListResponse>(`/devices${query ? `?${query}` : ''}`)
}

export const getDevice = (deviceId: string) =>
  fetchJson<DeviceDetailResponse>(`/devices/${deviceId}`)

export const updateDevicePolicy = (deviceId: string, policy: {
  scan_policy: string
  manually_opted_in?: boolean
  reason?: string
}) =>
  fetchJson<Device>(`/devices/${deviceId}/policy`, {
    method: 'PUT',
    body: JSON.stringify(policy),
  })

// Scans API
export interface Scan {
  id: string
  scan_type: string
  started_at: string
  completed_at: string | null
  status: string
  devices_found: number
  new_devices: number
  medical_devices_excluded: number
  triggered_by: string
}

export const getScans = (limit = 20) =>
  fetchJson<{ scans: Scan[]; total: number }>(`/scans?limit=${limit}`)

export const getLatestScan = () =>
  fetchJson<{ scan: Scan | null }>('/scans/latest')

export const triggerScan = (scanType = 'full') =>
  fetchJson<{ scan_id: string; status: string; message: string }>('/scans/trigger', {
    method: 'POST',
    body: JSON.stringify({ scan_type: scanType }),
  })

// Compliance API
export interface ComplianceSummary {
  total_devices: number
  assessed_devices: number
  medical_excluded: number
  compliance: {
    compliant: number
    drifted: number
    unknown: number
    excluded: number
    rate: number
  }
  generated_at: string
}

export const getComplianceSummary = () =>
  fetchJson<ComplianceSummary>('/compliance/summary')

export const getDriftedDevices = () =>
  fetchJson<{ drifted_count: number; devices: Device[] }>('/compliance/drifted')

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/client'

// Dashboard
export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: api.getDashboard,
    refetchInterval: 30000, // Refresh every 30s
  })
}

// Devices
export function useDevices(params?: Parameters<typeof api.getDevices>[0]) {
  return useQuery({
    queryKey: ['devices', params],
    queryFn: () => api.getDevices(params),
  })
}

export function useDevice(deviceId: string) {
  return useQuery({
    queryKey: ['device', deviceId],
    queryFn: () => api.getDevice(deviceId),
    enabled: !!deviceId,
  })
}

export function useUpdateDevicePolicy() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ deviceId, policy }: {
      deviceId: string
      policy: Parameters<typeof api.updateDevicePolicy>[1]
    }) => api.updateDevicePolicy(deviceId, policy),
    onSuccess: (_, { deviceId }) => {
      queryClient.invalidateQueries({ queryKey: ['device', deviceId] })
      queryClient.invalidateQueries({ queryKey: ['devices'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

// Scans
export function useScans(limit = 20) {
  return useQuery({
    queryKey: ['scans', limit],
    queryFn: () => api.getScans(limit),
  })
}

export function useLatestScan() {
  return useQuery({
    queryKey: ['latestScan'],
    queryFn: api.getLatestScan,
    refetchInterval: 10000, // Check for updates
  })
}

export function useTriggerScan() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (scanType?: string) => api.triggerScan(scanType),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scans'] })
      queryClient.invalidateQueries({ queryKey: ['latestScan'] })
    },
  })
}

// Compliance
export function useComplianceSummary() {
  return useQuery({
    queryKey: ['compliance', 'summary'],
    queryFn: api.getComplianceSummary,
  })
}

export function useDriftedDevices() {
  return useQuery({
    queryKey: ['compliance', 'drifted'],
    queryFn: api.getDriftedDevices,
  })
}

import type { DeviceSnapshot, IntegrationStatus, UsageStatus } from '@/types/device'

export type DeviceConnectionFilter = 'all' | 'online' | 'offline'
export type DeviceUsageFilter = 'all' | UsageStatus
export type DeviceIntegrationFilter = 'all' | IntegrationStatus

export function deviceConnectionState(device: DeviceSnapshot): 'online' | 'offline' {
  return device.integrationStatus === 'tested' && device.connectionStatus !== 'offline'
    ? 'online'
    : 'offline'
}

export interface DeviceFilters {
  keyword: string
  connection: DeviceConnectionFilter
  usage: DeviceUsageFilter
  integration: DeviceIntegrationFilter
}

export function filterDevices(devices: DeviceSnapshot[], filters: DeviceFilters): DeviceSnapshot[] {
  const query = filters.keyword.trim().toLowerCase()
  return devices.filter((device) => (
    (!query
      || device.blockId.toLowerCase().includes(query)
      || device.displayName.toLowerCase().includes(query)
      || device.gatewayName.toLowerCase().includes(query)
      || String(device.deviceId).includes(query))
    && (filters.integration === 'all' || device.integrationStatus === filters.integration)
    && (filters.connection === 'all' || deviceConnectionState(device) === filters.connection)
    && (filters.usage === 'all' || device.usageStatus === filters.usage)
  ))
}

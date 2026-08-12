import type { AxisPoint, MotionEvent, MotionState, MotionStatistics } from './motion'

// Compatibility re-exports keep the first commit buildable while Views migrate.
export type { AxisPoint, MotionState, MotionStatistics } from './motion'
export type DeviceEvent = MotionEvent

export type ConnectionStatus = 'online' | 'offline' | 'warning'
export type UsageStatus = 'idle' | 'collecting' | 'unavailable'
export type IntegrationStatus = 'tested' | 'pending-integration'
export type DataQualityStatus = 'good' | 'normal' | 'poor' | 'unknown'
export type SensorStatus = 'ok' | 'error' | 'unknown'

export interface DeviceFirmwareInfo {
  version: string
  motionProtocolVersion: number
  accelerationRangeG: number
  gyroscopeRangeDps: number
  sampleRateHz: number
  coordinateRateHz: number
  capabilities: number
}

export interface DeviceErrorCounters {
  sampleGapCount: number
  sensorErrorCount: number
  queueDropCount: number
  bleResourceWaitCount: number
  abnormalCount: number
}

export interface DeviceHealth {
  batteryVoltageMv: number | null
  batteryLow: boolean | null
  firmware: DeviceFirmwareInfo | null
  sensorStatus: SensorStatus
  calibrated: boolean | null
  streaming: boolean | null
  uptimeSeconds: number | null
  resetReason: string | null
  latestSampleSequence: number | null
  queueDepth: number | null
  errors: DeviceErrorCounters
}

export interface DeviceSnapshot {
  blockId: string
  deviceId: number
  displayName: string
  integrationStatus: IntegrationStatus
  connectionStatus: ConnectionStatus
  usageStatus: UsageStatus
  motionState: MotionState
  currentTaskId: string | null
  sessionId: string
  gatewayName: string
  lastDataAgoMs: number | null
  packetCount: number
  lostPacketCount: number
  dataQuality: DataQualityStatus
  batteryPercent: number | null
  health: DeviceHealth

  // Compatibility fields for the current review Views. New work reads these
  // values from MotionSnapshot/RealtimeMotionData through motion.store.ts.
  acceleration: AxisPoint[]
  gyroscope: AxisPoint[]
  statistics: MotionStatistics
  events: MotionEvent[]
}

export interface BlockMappingEntry {
  deviceId: number
  blockId: string
}

export interface BlockMapping {
  mappingVersion: string
  editableSource: string | null
  devices: BlockMappingEntry[]
}

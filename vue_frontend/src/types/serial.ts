import type { DeviceFirmwareInfo, DeviceHealth } from './device'

export type SerialReceiveStatus = 'idle' | 'receiving' | 'error'

export interface SerialPortInfo {
  port: string
  description: string
  nativeUsb: boolean
}

export interface SerialStatus {
  isOpen: boolean
  port: string | null
  baudRate: number
  receiveStatus: SerialReceiveStatus
  errorMessage: string | null
  byteCount: number
  frameCount: number
  encodedFrameCount: number
  gatewayLostFrames: number
  cobsErrorCount: number
  crcErrorCount: number
  frameErrorCount: number
  overflowCount: number
  hostSessionReady: boolean
  hostSessionId: number | null
  aggregator: AggregatorStatus | null
}

export interface AggregatorLinkStatus {
  gatewayId: number
  active: boolean
  rxQueuePending: boolean
  commandQueuePending: boolean
  uartPort: number
  rxQueueHighWatermark: number
  uartRxBytes: number
  validFrames: number
  cobsErrors: number
  crcErrors: number
  frameErrors: number
  frameDrops: number
  uartOutputDrops: number
  lastFrameAgeMs: number | null
}

export interface AggregatorStatus {
  usbReady: boolean
  outputDropSeen: boolean
  uptimeMs: number
  usbRxBytes: number
  usbTxBytes: number
  usbValidCommands: number
  usbInputErrors: number
  usbOutputDrops: number
  usbQueueHighWatermark: number
  ageSeconds: number | null
  links: AggregatorLinkStatus[]
}

export interface LinkDiagnostics {
  deviceId: number
  connected: boolean
  ready: boolean
  connectionIntervalMs: number | null
  attMtu: number | null
  dleValid: boolean
  dleRequestResult: string
  maxTxOctets: number | null
  maxTxTimeUs: number | null
  maxRxOctets: number | null
  maxRxTimeUs: number | null
  phyValid: boolean
  txPhy: string | null
  rxPhy: string | null
  rssiDbm: number | null
  receiveRate1sPps: number
  receiveRate10sPps: number
  receivedFrames: number
  sequenceGapFrames: number
  connectCount: number
  disconnectCount: number
  connectionUpdateCount: number
  invalidFrames: number
  lastDisconnectReason: string
  gatewayQueueDrops: number
  deviceQueueDepth: number | null
  deviceQueueDrops: number
  bleResourceWaitCount: number
  diagnosticAgeSeconds: number | null
  linkLayerRetransmissions: number | null
  linkLayerRetransmissionsNote: string
}

export type DeviceCommand =
  | 'START_STREAM'
  | 'STOP_STREAM'
  | 'GET_INFO'
  | 'GET_STATUS'
  | 'RESET_STATS'
  | 'RECALIBRATE'
  | 'REBOOT'

export type DeviceCommandFailureCode =
  | 'serial-closed'
  | 'pending-integration'
  | 'unsupported-command'

export interface DeviceCommandResult {
  ok: boolean
  deviceId: number
  command: DeviceCommand
  requestId: number
  resultCode: number
  failureCode: DeviceCommandFailureCode | null
  message: string
  executedAt: string
  data: DeviceFirmwareInfo | DeviceHealth | null
}

export interface BatteryRefreshItem {
  deviceId: number
  ok: boolean
  batteryVoltageMv: number | null
  batteryLow: boolean | null
  message: string
}

export interface BatteryRefreshResult {
  requested: number
  succeeded: number
  skipped: number
  results: BatteryRefreshItem[]
}

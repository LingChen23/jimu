import type { LinkDiagnostics, SerialPortInfo, SerialStatus } from '@/types/serial'

export const mockSerialPorts: SerialPortInfo[] = [
  { port: 'COM3', description: 'USB Serial/JTAG Controller（Mock）', nativeUsb: true },
  { port: 'COM7', description: 'USB 串口（Mock）', nativeUsb: false },
]

export const mockSerialStatus: SerialStatus = {
  isOpen: false,
  port: null,
  baudRate: 921_600,
  receiveStatus: 'idle',
  errorMessage: null,
  byteCount: 0,
  frameCount: 0,
  encodedFrameCount: 0,
  gatewayLostFrames: 0,
  cobsErrorCount: 0,
  crcErrorCount: 0,
  frameErrorCount: 0,
  overflowCount: 0,
  hostSessionReady: false,
  hostSessionId: null,
  aggregator: null,
}

function makeLinkDiagnostics(deviceId: number): LinkDiagnostics {
  const tested = deviceId <= 4
  const warning = deviceId === 4
  return {
    deviceId,
    connected: tested,
    ready: tested && !warning,
    connectionIntervalMs: tested ? 15 : null,
    attMtu: tested ? 247 : null,
    dleValid: tested,
    dleRequestResult: tested ? '成功' : '未请求',
    maxTxOctets: tested ? 251 : null,
    maxTxTimeUs: tested ? 2_120 : null,
    maxRxOctets: tested ? 251 : null,
    maxRxTimeUs: tested ? 2_120 : null,
    phyValid: tested,
    txPhy: tested ? '2M' : null,
    rxPhy: tested ? '2M' : null,
    rssiDbm: tested ? -48 - deviceId * 3 : null,
    receiveRate1sPps: tested ? 48 - deviceId : 0,
    receiveRate10sPps: tested ? 47.5 - deviceId * 0.7 : 0,
    receivedFrames: tested ? 9_500 + deviceId * 427 : 0,
    sequenceGapFrames: tested ? (warning ? 18 : deviceId - 1) : 0,
    connectCount: tested ? 1 + (deviceId % 2) : 0,
    disconnectCount: tested ? (warning ? 2 : 0) : 0,
    connectionUpdateCount: tested ? 1 : 0,
    invalidFrames: tested ? (warning ? 3 : 0) : 0,
    lastDisconnectReason: warning ? '连接超时 (0x0008)' : '无',
    gatewayQueueDrops: warning ? 4 : 0,
    deviceQueueDepth: tested ? (warning ? 5 : deviceId % 3) : null,
    deviceQueueDrops: warning ? 4 : 0,
    bleResourceWaitCount: tested ? deviceId * 2 : 0,
    diagnosticAgeSeconds: tested ? 0.4 + deviceId * 0.1 : null,
    linkLayerRetransmissions: null,
    linkLayerRetransmissionsNote: 'NimBLE 公开接口不提供链路层真实重传次数',
  }
}

export const mockLinkDiagnostics: Record<number, LinkDiagnostics> = Object.fromEntries(
  Array.from({ length: 12 }, (_, index) => {
    const deviceId = index + 1
    return [deviceId, makeLinkDiagnostics(deviceId)]
  }),
)

import type { DeviceHealth, DeviceSnapshot } from '@/types/device'
import {
  makeAccelerationSeries,
  makeGyroscopeSeries,
  makeMotionEvents,
  makeMotionStatistics,
} from './motion'

function makeHealth(deviceId: number, tested: boolean): DeviceHealth {
  if (!tested) {
    return {
      batteryVoltageMv: null,
      batteryLow: null,
      firmware: null,
      sensorStatus: 'unknown',
      calibrated: null,
      streaming: null,
      uptimeSeconds: null,
      resetReason: null,
      latestSampleSequence: null,
      queueDepth: null,
      errors: {
        sampleGapCount: 0,
        sensorErrorCount: 0,
        queueDropCount: 0,
        bleResourceWaitCount: 0,
        abnormalCount: 0,
      },
    }
  }

  const batteryVoltageMv = [0, 4050, 3920, 3760, 3290][deviceId] ?? 3800
  const warning = deviceId === 4
  return {
    batteryVoltageMv,
    batteryLow: warning,
    firmware: {
      version: '1.3.0',
      motionProtocolVersion: 1,
      accelerationRangeG: 8,
      gyroscopeRangeDps: 1000,
      sampleRateHz: 100,
      coordinateRateHz: 50,
      capabilities: 31,
    },
    sensorStatus: warning ? 'error' : 'ok',
    calibrated: true,
    streaming: deviceId === 3,
    uptimeSeconds: 7_200 + deviceId * 1_337,
    resetReason: '上电复位',
    latestSampleSequence: 84_200 + deviceId * 811,
    queueDepth: warning ? 5 : deviceId % 3,
    errors: {
      sampleGapCount: warning ? 12 : deviceId - 1,
      sensorErrorCount: warning ? 2 : 0,
      queueDropCount: warning ? 4 : 0,
      bleResourceWaitCount: deviceId * 2,
      abnormalCount: warning ? 2 : deviceId % 2,
    },
  }
}

export const mockDevices: DeviceSnapshot[] = Array.from({ length: 12 }, (_, index) => {
  const deviceId = index + 1
  const number = String(deviceId).padStart(2, '0')
  const tested = deviceId <= 4
  const warning = deviceId === 4
  const collecting = deviceId === 3
  const motionState = tested ? (deviceId === 2 || collecting ? 'moving' : 'stationary') : 'unknown'

  return {
    blockId: `BLOCK-${number}`,
    deviceId,
    displayName: `积木${number}`,
    integrationStatus: tested ? 'tested' : 'pending-integration',
    connectionStatus: tested ? 'online' : 'offline',
    usageStatus: tested ? (collecting ? 'collecting' : 'idle') : 'unavailable',
    motionState,
    currentTaskId: collecting ? 'T20260729-001' : null,
    sessionId: tested ? `S-${483700 + deviceId}` : '待接入',
    gatewayName: tested ? '网关A' : '待分配',
    lastDataAgoMs: tested ? 42 + deviceId * 17 : null,
    packetCount: tested ? 9_500 + deviceId * 427 : 0,
    lostPacketCount: tested ? (warning ? 18 : deviceId - 1) : 0,
    dataQuality: tested ? (warning ? 'poor' : 'good') : 'unknown',
    batteryPercent: null,
    health: makeHealth(deviceId, tested),
    acceleration: tested ? makeAccelerationSeries(deviceId) : [],
    gyroscope: tested ? makeGyroscopeSeries(deviceId) : [],
    statistics: makeMotionStatistics(deviceId, tested),
    events: makeMotionEvents(deviceId, tested),
  }
})

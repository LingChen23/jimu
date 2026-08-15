import { describe, expect, it } from 'vitest'
import type { DeviceSnapshot } from '@/types/device'
import {
  batteryLevelText,
  batteryVoltageText,
  connectionText,
  integrationText,
  streamingText,
} from './device.formatters'

function device(deviceId: number, batteryVoltageMv: number | null, streaming: boolean | null): DeviceSnapshot {
  return {
    blockId: String.fromCharCode(64 + deviceId), deviceId, displayName: `积木 ${deviceId}`,
    integrationStatus: 'tested', connectionStatus: 'online', usageStatus: 'idle', motionState: 'stationary',
    currentTaskId: null, sessionId: '1', gatewayName: '网关A', lastDataAgoMs: 0, packetCount: 0,
    lostPacketCount: 0, dataQuality: 'good', batteryPercent: null, acceleration: [], gyroscope: [], events: [],
    statistics: { movingSeconds: 0, stationarySeconds: 0, pauseCount: 0, pauseTotalSeconds: 0, strongShakeCount: 0, flipCount: 0, rotationCount: 0, spatialRotationDegrees: 0, spatialRotationTurns: 0, dropCount: 0, abnormalCount: 0 },
    health: { batteryVoltageMv, batteryLow: null, firmware: null, sensorStatus: 'ok', calibrated: true, streaming, uptimeSeconds: null, resetReason: null, latestSampleSequence: null, queueDepth: null, errors: { sampleGapCount: 0, sensorErrorCount: 0, queueDropCount: 0, bleResourceWaitCount: 0, abnormalCount: 0 } },
  }
}

const mockDevices = [device(1, 4050, false), device(2, 3760, true), device(3, 3290, false), device(4, null, null)]

describe('device page helpers', () => {
  it('formats online devices without exposing a generic warning state', () => {
    expect(connectionText(mockDevices[3])).toBe('在线')
  })

  it('uses explicit pending and missing-value labels', () => {
    const pending = { ...mockDevices[3], integrationStatus: 'pending-integration' as const, connectionStatus: 'offline' as const }
    expect(integrationText(pending)).toBe('待接入')
    expect(connectionText(pending)).toBe('未接入')
    expect(batteryVoltageText(pending)).toBe('—')
    expect(batteryLevelText(mockDevices[0])).toBe('高')
    expect(batteryLevelText(mockDevices[1])).toBe('中')
    expect(batteryLevelText(mockDevices[2])).toBe('低')
    expect(streamingText(mockDevices[1])).toBe('数据流开启')
  })
})

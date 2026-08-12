import { describe, expect, it } from 'vitest'
import { mockDevices } from '@/mocks/devices'
import { filterDevices } from './device.filters'
import {
  batteryLevelText,
  batteryVoltageText,
  connectionText,
  integrationText,
  streamingText,
} from './device.formatters'

describe('device page helpers', () => {
  it('filters tested collecting devices without exposing a generic warning state', () => {
    const result = filterDevices(mockDevices, {
      keyword: '',
      connection: 'online',
      usage: 'collecting',
      integration: 'tested',
    })
    expect(result.map((device) => device.deviceId)).toEqual([3])
    expect(connectionText(mockDevices[3])).toBe('在线')
  })

  it('uses explicit pending and missing-value labels', () => {
    expect(integrationText(mockDevices[4])).toBe('待接入')
    expect(connectionText(mockDevices[4])).toBe('未接入')
    expect(batteryVoltageText(mockDevices[4])).toBe('—')
    expect(batteryLevelText(mockDevices[0])).toBe('高')
    expect(batteryLevelText(mockDevices[2])).toBe('中')
    expect(batteryLevelText(mockDevices[3])).toBe('低')
    expect(streamingText(mockDevices[2])).toBe('数据流开启')
  })
})

import { describe, expect, it } from 'vitest'
import { createMotionDataService } from './data.service'

describe('motion data service factory', () => {
  it('creates the configured mock provider', async () => {
    const service = createMotionDataService('mock')
    const devices = await service.listDevices()

    expect(devices).toHaveLength(12)
    expect(devices.filter((device) => device.integrationStatus === 'tested')).toHaveLength(4)
  })

  it('keeps provider instances isolated', async () => {
    const first = createMotionDataService('mock')
    const second = createMotionDataService('mock')
    await first.openSerial({ port: 'COM3', baudRate: 115200 })

    expect((await first.getSerialStatus()).isOpen).toBe(true)
    expect((await second.getSerialStatus()).isOpen).toBe(false)
  })

  it('rejects unsupported data sources', () => {
    expect(() => createMotionDataService('remote')).toThrow('不支持的数据源')
  })
})

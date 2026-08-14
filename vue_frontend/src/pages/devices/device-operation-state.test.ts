import { describe, expect, it } from 'vitest'
import {
  batteryButtonState,
  commandButtonState,
  counterButtonState,
} from './device-operation-state'

describe('device operation state isolation', () => {
  it('counter reset only changes the counter button', () => {
    const counter = counterButtonState(true, true, true)
    const command = commandButtonState(true, false)
    const battery = batteryButtonState(true, false)

    expect(counter).toEqual({ disabled: true, label: '处理中…' })
    expect(command.disabled).toBe(false)
    expect(battery).toEqual({ disabled: false, label: '一键获取电池电压' })
  })
})

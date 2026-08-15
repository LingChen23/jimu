import { describe, expect, it } from 'vitest'
import {
  relativeSeconds,
  relativeTimeAxisMaximum,
  relativeTimeLabel,
  relativeTimeTickInterval,
} from './axis-chart-time'

describe('axis chart relative time', () => {
  it('starts every movement at zero instead of displaying local clock time', () => {
    expect(relativeSeconds([
      { timestamp: 10_000, x: 0, y: 0, z: 0 },
      { timestamp: 12_500, x: 0, y: 0, z: 0 },
      { timestamp: 20_000, x: 0, y: 0, z: 0 },
    ])).toEqual([0, 2.5, 10])
  })

  it('formats axis and hover time to one hundredth of a second', () => {
    expect(relativeTimeLabel(0)).toBe('0.00秒')
    expect(relativeTimeLabel(5)).toBe('5.00秒')
    expect(relativeTimeLabel(10.126)).toBe('10.13秒')
  })

  it('uses readable five-second ticks for ordinary complete movements', () => {
    expect(relativeTimeTickInterval(10)).toBe(5)
    expect(relativeTimeTickInterval(30)).toBe(5)
    expect(relativeTimeAxisMaximum(9.94)).toBe(10)
  })
})

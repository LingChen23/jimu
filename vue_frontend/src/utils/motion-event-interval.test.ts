import { describe, expect, it } from 'vitest'
import type { AxisPoint } from '@/types/motion'
import { estimateAngularEventStart } from './motion-event-interval'

describe('angular event interval estimation', () => {
  it('integrates angular velocity backwards to find the event start', () => {
    const series: AxisPoint[] = Array.from({ length: 101 }, (_, index) => ({
      timestamp: index * 10,
      x: 360,
      y: 0,
      z: 0,
    }))

    expect(estimateAngularEventStart(series, 1_000, 180, 600)).toBeCloseTo(500, 5)
  })

  it('uses a bounded fallback when samples are unavailable', () => {
    expect(estimateAngularEventStart([], 2_000, 330, 1_000)).toBe(1_000)
    expect(estimateAngularEventStart([], 300, 330, 1_000)).toBe(0)
  })
})

import { describe, expect, it } from 'vitest'
import type { PcInertialTrajectory } from '@/types/motion'
import { mergeLiveTrajectory } from './motion.store'

const trajectory = (points: Array<[number, number, number]>): PcInertialTrajectory => ({
  source: 'pc-inertial',
  coordinateSystem: 'world-relative',
  unit: 'm',
  metricReliable: false,
  points: points.map(([x, y, z], timestamp) => ({ timestamp, x, y, z })),
})

describe('live trajectory accumulation', () => {
  it('appends changed live positions without duplicating stationary positions', () => {
    const initial = trajectory([[0, 0, 0], [1, 0, 0]])
    const appended = mergeLiveTrajectory(initial, trajectory([[2, 0, 0]]), false)
    const stationary = mergeLiveTrajectory(appended, trajectory([[2, 0, 0]]), false)

    expect(appended.points.map((point) => point.x)).toEqual([0, 1, 2])
    expect(stationary.points).toHaveLength(3)
  })

  it('starts a new path when the backend trajectory revision changes', () => {
    const reset = mergeLiveTrajectory(trajectory([[0, 0, 0], [1, 0, 0]]), trajectory([[5, 1, 0]]), true)
    expect(reset.points).toHaveLength(1)
    expect(reset.points[0]?.x).toBe(5)
  })
})

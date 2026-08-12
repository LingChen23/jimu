import { describe, expect, it } from 'vitest'
import {
  prepareTrajectory,
  projectTrajectory,
  trajectoryProjectionBounds,
} from './trajectory'

describe('trajectory helpers', () => {
  const points = [
    { timestamp: 1, x: 10, y: 20, z: 30 },
    { timestamp: 2, x: 12, y: 24, z: 36 },
  ]

  it('preserves metric coordinates and normalizes shape coordinates', () => {
    expect(prepareTrajectory(points, 'metric').points).toEqual([[10, 20, 30], [12, 24, 36]])
    const shape = prepareTrajectory(points, 'shape')
    expect(shape.origin).toEqual([10, 20, 30])
    expect(shape.points[0]).toEqual([0, 0, 0])
  })

  it('projects axes and keeps equal display aspect', () => {
    expect(projectTrajectory([[1, 2, 3]], 'xz')).toEqual([[1, 3]])
    const bounds = trajectoryProjectionBounds([[[0, 0, 0], [2, 1, 0]]], 'xy', 2, 1)
    const width = bounds.maximumHorizontal - bounds.minimumHorizontal
    const height = bounds.maximumVertical - bounds.minimumVertical
    expect(width / height).toBeCloseTo(2)
  })
})

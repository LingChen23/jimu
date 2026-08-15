import { describe, expect, it } from 'vitest'
import type { MovementDetail } from '@/types/motion'
import { aggregateTaskMovements } from './task-result-aggregate'

function detail(speedValues: number[], movingSeconds: number, shakeCount: number): MovementDetail {
  return {
    movementId: 1, deviceId: 1, analysisMode: 'pc', state: 'completed',
    startedAt: '', endedAt: '', durationSeconds: movingSeconds, movingSeconds,
    sampleCount: speedValues.length,
    statistics: {
      movingSeconds, stationarySeconds: 0, pauseCount: 1, pauseTotalSeconds: .8,
      strongShakeCount: shakeCount, flipCount: 1, rotationCount: 2,
      spatialRotationDegrees: 360, spatialRotationTurns: 1, dropCount: 0,
      abnormalCount: 0,
    },
    speed: {
      maximumMps: 0, p05Mps: 0, averageMps: 0, medianMps: 0,
      standardDeviationMps: 0, p05Percentile: 5, sampleCount: speedValues.length,
    },
    tremor: {
      valid: true, dominantFrequencyHz: 6, intensityDps: 20, bandRmsDps: 10,
      frequencyBandHz: [3, 20], reason: null,
    },
    acceleration: [], gyroscope: [], dynamicAcceleration: [],
    speedSeries: speedValues.map((value, index) => ({ timestamp: index, value })),
    orientation: {
      initialized: false, quaternion: { w: 1, x: 0, y: 0, z: 0 }, rollDegrees: 0,
      pitchDegrees: 0, yawDegrees: 0, yawReference: 'relative',
      positionM: { x: 0, y: 0, z: 0 }, velocityMps: { x: 0, y: 0, z: 0 },
    },
    events: [], pauseEvents: [], dropEvents: [],
    trajectories: {
      device: { source: 'device', coordinateSystem: 'device-relative', unit: 'm', metricReliable: false, points: [] },
      pcInertial: { source: 'pc-inertial', coordinateSystem: 'world-relative', unit: 'm', metricReliable: false, points: [] },
    },
    sessionTremorAverage: { validMovementCount: 1, totalMovementCount: 1, meanFrequencyHz: 6, meanIntensityDps: 20, aggregation: '' },
  }
}

describe('aggregateTaskMovements', () => {
  it('sums movement statistics and excludes boundary zeros from P05 speed', () => {
    const aggregate = aggregateTaskMovements([
      detail([0, 1, 2], 3, 1),
      detail([0, 3, 4], 5, 2),
    ])

    expect(aggregate.statistics?.movingSeconds).toBe(8)
    expect(aggregate.statistics?.strongShakeCount).toBe(3)
    expect(aggregate.statistics?.rotationCount).toBe(4)
    expect(aggregate.speed?.sampleCount).toBe(4)
    expect(aggregate.speed?.p05Mps).toBeGreaterThan(0)
    expect(aggregate.tremor?.dominantFrequencyHz).toBe(6)
  })

  it('returns empty aggregates when no movement details exist', () => {
    expect(aggregateTaskMovements([])).toEqual({ statistics: null, speed: null, tremor: null })
  })
})

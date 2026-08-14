import type { MotionStatistics, MovementDetail, SpeedStatistics, TremorMetrics } from '@/types/motion'

export interface TaskMotionAggregate {
  statistics: MotionStatistics | null
  speed: SpeedStatistics | null
  tremor: TremorMetrics | null
}

function percentile(sorted: number[], percent: number): number {
  if (!sorted.length) return 0
  const position = (sorted.length - 1) * percent
  const lower = Math.floor(position)
  const upper = Math.ceil(position)
  if (lower === upper) return sorted[lower]
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower)
}

export function aggregateTaskMovements(details: MovementDetail[]): TaskMotionAggregate {
  if (!details.length) return { statistics: null, speed: null, tremor: null }

  const statistics = details.reduce<MotionStatistics>((total, detail) => {
    const value = detail.statistics
    return {
      movingSeconds: total.movingSeconds + value.movingSeconds,
      stationarySeconds: total.stationarySeconds + value.stationarySeconds,
      pauseCount: total.pauseCount + value.pauseCount,
      pauseTotalSeconds: total.pauseTotalSeconds + value.pauseTotalSeconds,
      strongShakeCount: total.strongShakeCount + value.strongShakeCount,
      flipCount: total.flipCount + value.flipCount,
      rotationCount: total.rotationCount + value.rotationCount,
      spatialRotationDegrees: total.spatialRotationDegrees + value.spatialRotationDegrees,
      spatialRotationTurns: total.spatialRotationTurns + value.spatialRotationTurns,
      dropCount: total.dropCount + value.dropCount,
      suspectedDropCount: total.suspectedDropCount + value.suspectedDropCount,
      abnormalCount: total.abnormalCount + value.abnormalCount,
    }
  }, {
    movingSeconds: 0, stationarySeconds: 0, pauseCount: 0, pauseTotalSeconds: 0,
    strongShakeCount: 0, flipCount: 0, rotationCount: 0, spatialRotationDegrees: 0,
    spatialRotationTurns: 0, dropCount: 0, suspectedDropCount: 0, abnormalCount: 0,
  })

  const speeds = details.flatMap((detail) => detail.speedSeries.map((point) => point.value))
    // Completed movement curves include zero-speed boundary samples. The
    // displayed minimum follows the existing P05 semantics and excludes those
    // boundary zeros instead of reporting a misleading 0 m/s.
    .filter((value) => Number.isFinite(value) && value > 0)
    .sort((left, right) => left - right)
  const speed = speeds.length ? (() => {
    const average = speeds.reduce((sum, value) => sum + value, 0) / speeds.length
    const variance = speeds.reduce((sum, value) => sum + (value - average) ** 2, 0) / speeds.length
    return {
      minimumMps: percentile(speeds, .05),
      maximumMps: speeds.at(-1) ?? 0,
      p05Mps: percentile(speeds, .05),
      averageMps: average,
      medianMps: percentile(speeds, .5),
      standardDeviationMps: Math.sqrt(variance),
      p05Percentile: 5 as const,
      sampleCount: speeds.length,
    }
  })() : null

  const validTremors = details.map((detail) => detail.tremor).filter((value) => value.valid)
  const frequencies = validTremors.flatMap((value) => value.dominantFrequencyHz === null ? [] : [value.dominantFrequencyHz])
  const intensities = validTremors.flatMap((value) => value.intensityDps === null ? [] : [value.intensityDps])
  const bandRms = validTremors.flatMap((value) => value.bandRmsDps === null ? [] : [value.bandRmsDps])
  const average = (values: number[]): number | null => values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : null
  const tremor: TremorMetrics = {
    valid: validTremors.length > 0,
    dominantFrequencyHz: average(frequencies),
    intensityDps: average(intensities),
    bandRmsDps: average(bandRms),
    frequencyBandHz: validTremors[0]?.frequencyBandHz ?? [3, 20],
    reason: validTremors.length ? null : '本次任务没有可汇总的震颤特征',
  }

  return { statistics, speed, tremor }
}

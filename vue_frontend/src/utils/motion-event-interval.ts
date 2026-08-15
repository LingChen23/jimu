import type { AxisPoint } from '@/types/motion'

function angularRate(point: AxisPoint): number {
  return Math.hypot(point.x, point.y, point.z)
}

/**
 * Estimate where an angular event began by integrating the stored gyroscope
 * samples backwards from its confirmed completion time.
 */
export function estimateAngularEventStart(
  series: AxisPoint[],
  endTimestamp: number,
  targetDegrees: number,
  fallbackDurationMs: number,
): number {
  const end = Math.max(0, endTimestamp)
  if (!series.length || targetDegrees <= 0) return Math.max(0, end - fallbackDurationMs)

  let endIndex = series.length - 1
  while (endIndex > 0 && series[endIndex].timestamp > end) endIndex -= 1
  let accumulatedDegrees = 0
  const maximumLookbackMs = Math.max(8_000, fallbackDurationMs * 4)

  for (let index = endIndex; index > 0; index -= 1) {
    const current = series[index]
    const previous = series[index - 1]
    if (end - previous.timestamp > maximumLookbackMs) break
    const rawDeltaSeconds = (current.timestamp - previous.timestamp) / 1_000
    if (!Number.isFinite(rawDeltaSeconds) || rawDeltaSeconds <= 0) continue
    // Do not turn a recording gap into artificial angular travel.
    const deltaSeconds = Math.min(rawDeltaSeconds, 0.05)
    const intervalDegrees = (
      (angularRate(current) + angularRate(previous)) * 0.5 * deltaSeconds
    )
    if (!Number.isFinite(intervalDegrees) || intervalDegrees <= 0) continue
    if (accumulatedDegrees + intervalDegrees >= targetDegrees) {
      const needed = targetDegrees - accumulatedDegrees
      const fraction = Math.min(1, Math.max(0, needed / intervalDegrees))
      return Math.max(0, current.timestamp - fraction * rawDeltaSeconds * 1_000)
    }
    accumulatedDegrees += intervalDegrees
  }

  return Math.max(0, end - fallbackDurationMs)
}

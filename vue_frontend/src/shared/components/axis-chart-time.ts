import type { AxisPoint } from '@/types/motion'

export function relativeSeconds(points: AxisPoint[]): number[] {
  const origin = points.find((point) => Number.isFinite(point.timestamp))?.timestamp ?? 0
  return points.map((point) => Math.max(0, (point.timestamp - origin) / 1_000))
}

export function relativeTimeLabel(seconds: number): string {
  return `${Number.isFinite(seconds) ? seconds.toFixed(2) : '0.00'}秒`
}

export function relativeTimeTickInterval(durationSeconds: number): number {
  if (durationSeconds <= 1) return 0.2
  if (durationSeconds <= 5) return 1
  if (durationSeconds <= 30) return 5
  if (durationSeconds <= 60) return 10
  return Math.ceil(durationSeconds / 30) * 5
}

export function relativeTimeAxisMaximum(durationSeconds: number): number {
  const interval = relativeTimeTickInterval(durationSeconds)
  return Math.max(interval, Math.ceil(durationSeconds / interval) * interval)
}

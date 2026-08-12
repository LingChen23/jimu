import type { AxisPoint } from '@/types/motion'

export const MOCK_BASE_TIME_MS = Date.parse('2026-07-29T14:20:00+08:00')

export function roundMockValue(value: number, digits = 4): number {
  const factor = 10 ** digits
  return Math.round(value * factor) / factor
}

export function makeAccelerationSeries(deviceId: number, count = 120): AxisPoint[] {
  const start = MOCK_BASE_TIME_MS + deviceId * 60_000
  return Array.from({ length: count }, (_, index) => ({
    timestamp: start + index * 100,
    x: roundMockValue(Math.sin((index + deviceId) / 8) * 1.35 + (index % 43 === 0 ? 2.1 : 0)),
    y: roundMockValue(Math.cos((index + deviceId) / 11) * 1.05),
    z: roundMockValue(9.81 + Math.sin((index + deviceId) / 13) * 0.7),
  }))
}

export function makeGyroscopeSeries(deviceId: number, count = 120): AxisPoint[] {
  const acceleration = makeAccelerationSeries(deviceId, count)
  return acceleration.map((point, index) => ({
    timestamp: point.timestamp,
    x: roundMockValue(point.x * 34 + Math.sin(index / 5) * 18),
    y: roundMockValue(point.y * 31 + Math.cos(index / 7) * 14),
    z: roundMockValue((point.z - 9.81) * 24),
  }))
}

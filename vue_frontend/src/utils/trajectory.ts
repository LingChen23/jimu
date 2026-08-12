import type { TrajectoryPoint } from '@/types/motion'

export type TrajectoryDisplayMode = 'shape' | 'metric'
export type TrajectoryView = 'iso' | 'xy' | 'xz' | 'yz'
export type TrajectoryProjectionView = Exclude<TrajectoryView, 'iso'>
export type TrajectoryVector = [number, number, number]
export type TrajectoryPoint2D = [number, number]

export interface TrajectoryProjection {
  horizontalAxis: 'X' | 'Y'
  verticalAxis: 'Y' | 'Z'
  horizontalIndex: 0 | 1
  verticalIndex: 1 | 2
}

export interface TrajectoryProjectionBounds {
  minimumHorizontal: number
  maximumHorizontal: number
  minimumVertical: number
  maximumVertical: number
}

export interface PreparedTrajectory {
  origin: TrajectoryVector
  scale: number
  points: TrajectoryVector[]
}

const projections: Record<TrajectoryProjectionView, TrajectoryProjection> = {
  xy: { horizontalAxis: 'X', verticalAxis: 'Y', horizontalIndex: 0, verticalIndex: 1 },
  xz: { horizontalAxis: 'X', verticalAxis: 'Z', horizontalIndex: 0, verticalIndex: 2 },
  yz: { horizontalAxis: 'Y', verticalAxis: 'Z', horizontalIndex: 1, verticalIndex: 2 },
}

function isFinitePoint(point: TrajectoryPoint): boolean {
  return Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z)
}

function vectorNorm(point: TrajectoryVector): number {
  return Math.hypot(point[0], point[1], point[2])
}

function decimate(points: TrajectoryVector[], maximumPoints: number): TrajectoryVector[] {
  if (points.length <= maximumPoints) return points
  const stride = Math.ceil(points.length / maximumPoints)
  const reduced = points.filter((_, index) => index % stride === 0)
  const last = points.at(-1)
  if (last && reduced.at(-1) !== last) reduced.push(last)
  return reduced
}

export function prepareTrajectory(
  points: TrajectoryPoint[],
  mode: TrajectoryDisplayMode,
  maximumPoints = 4000,
): PreparedTrajectory {
  const source = points
    .filter(isFinitePoint)
    .map<TrajectoryVector>((point) => [point.x, point.y, point.z])

  if (!source.length || mode === 'metric') {
    return {
      origin: [0, 0, 0],
      scale: 1,
      points: decimate(source, maximumPoints),
    }
  }

  const origin: TrajectoryVector = [...source[0]]
  const centered = source.map<TrajectoryVector>((point) => [
    point[0] - origin[0],
    point[1] - origin[1],
    point[2] - origin[2],
  ])
  const minima = [0, 1, 2].map((axis) => Math.min(...centered.map((point) => point[axis])))
  const maxima = [0, 1, 2].map((axis) => Math.max(...centered.map((point) => point[axis])))
  const axisExtent = Math.max(...maxima.map((value, axis) => value - minima[axis]))
  const radialExtent = Math.max(...centered.map(vectorNorm), 0)
  const scale = Math.max(axisExtent, radialExtent, 1e-6)
  const normalized = centered.map<TrajectoryVector>((point) => [
    point[0] / scale,
    point[1] / scale,
    point[2] / scale,
  ])

  return { origin, scale, points: decimate(normalized, maximumPoints) }
}

export function trajectoryProjection(view: TrajectoryProjectionView): TrajectoryProjection {
  return projections[view]
}

export function projectTrajectory(
  points: TrajectoryVector[],
  view: TrajectoryProjectionView,
): TrajectoryPoint2D[] {
  const projection = trajectoryProjection(view)
  return points.map((point) => [
    point[projection.horizontalIndex],
    point[projection.verticalIndex],
  ])
}

export function trajectoryProjectionBounds(
  paths: TrajectoryVector[][],
  view: TrajectoryProjectionView,
  aspectRatio: number,
  fallbackSpan: number,
  paddingRatio = 0.08,
): TrajectoryProjectionBounds {
  const points = paths.flatMap((path) => projectTrajectory(path, view))
  const safeAspectRatio = Number.isFinite(aspectRatio) && aspectRatio > 0 ? aspectRatio : 1
  const safeFallbackSpan = Number.isFinite(fallbackSpan) && fallbackSpan > 0 ? fallbackSpan : 1

  if (!points.length) {
    const horizontalSpan = safeAspectRatio >= 1 ? safeFallbackSpan * safeAspectRatio : safeFallbackSpan
    const verticalSpan = safeAspectRatio >= 1 ? safeFallbackSpan : safeFallbackSpan / safeAspectRatio
    return {
      minimumHorizontal: -horizontalSpan / 2,
      maximumHorizontal: horizontalSpan / 2,
      minimumVertical: -verticalSpan / 2,
      maximumVertical: verticalSpan / 2,
    }
  }

  const horizontalValues = points.map((point) => point[0])
  const verticalValues = points.map((point) => point[1])
  const minimumHorizontal = Math.min(...horizontalValues)
  const maximumHorizontal = Math.max(...horizontalValues)
  const minimumVertical = Math.min(...verticalValues)
  const maximumVertical = Math.max(...verticalValues)
  const horizontalCenter = (minimumHorizontal + maximumHorizontal) / 2
  const verticalCenter = (minimumVertical + maximumVertical) / 2
  const rawHorizontalSpan = maximumHorizontal - minimumHorizontal
  const rawVerticalSpan = maximumVertical - minimumVertical
  const referenceSpan = Math.max(rawHorizontalSpan, rawVerticalSpan, 1e-12)
  const nonDegenerateReference = referenceSpan <= 1e-12 ? safeFallbackSpan : referenceSpan
  const minimumAxisSpan = nonDegenerateReference * 0.04
  const paddingMultiplier = 1 + Math.max(0, paddingRatio) * 2
  let horizontalSpan = Math.max(rawHorizontalSpan, minimumAxisSpan) * paddingMultiplier
  let verticalSpan = Math.max(rawVerticalSpan, minimumAxisSpan) * paddingMultiplier

  if (horizontalSpan / verticalSpan < safeAspectRatio) {
    horizontalSpan = verticalSpan * safeAspectRatio
  } else {
    verticalSpan = horizontalSpan / safeAspectRatio
  }

  return {
    minimumHorizontal: horizontalCenter - horizontalSpan / 2,
    maximumHorizontal: horizontalCenter + horizontalSpan / 2,
    minimumVertical: verticalCenter - verticalSpan / 2,
    maximumVertical: verticalCenter + verticalSpan / 2,
  }
}

export function metricBoxDimensions(paths: TrajectoryVector[][]): {
  boxWidth: number
  boxHeight: number
  boxDepth: number
} {
  const points = paths.flat()
  if (!points.length) return { boxWidth: 100, boxHeight: 100, boxDepth: 100 }
  const extents = [0, 1, 2].map((axis) => {
    const values = points.map((point) => point[axis])
    return Math.max(...values) - Math.min(...values)
  })
  const largest = Math.max(...extents, 1e-6)
  const dimension = (extent: number) => Math.max(4, (extent / largest) * 100)
  return {
    boxWidth: dimension(extents[0]),
    boxDepth: dimension(extents[1]),
    boxHeight: dimension(extents[2]),
  }
}

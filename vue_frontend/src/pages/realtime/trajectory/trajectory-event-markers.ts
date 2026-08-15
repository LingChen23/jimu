import type { MotionEvent } from '@/types/motion'

const TRAJECTORY_MARKER_EVENT_TYPES = new Set<MotionEvent['type']>([
  'pause', 'drop', 'flip', 'rotation',
])
const TRAJECTORY_INTERVAL_EVENT_TYPES = new Set<MotionEvent['type']>([
  'pause', 'drop', 'flip', 'rotation',
])

export function isTrajectoryMarkerEvent(event: MotionEvent): boolean {
  return TRAJECTORY_MARKER_EVENT_TYPES.has(event.type)
}

export function isTrajectoryIntervalEvent(event: MotionEvent): boolean {
  return TRAJECTORY_INTERVAL_EVENT_TYPES.has(event.type)
    && Number.isFinite(event.startTimestamp)
    && Number.isFinite(event.endTimestamp)
    && Number(event.endTimestamp) > Number(event.startTimestamp)
}

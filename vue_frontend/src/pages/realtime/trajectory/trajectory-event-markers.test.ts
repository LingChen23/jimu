import { describe, expect, it } from 'vitest'
import type { MotionEvent, MotionEventType } from '@/types/motion'
import { isTrajectoryIntervalEvent, isTrajectoryMarkerEvent } from './trajectory-event-markers'

function event(type: MotionEventType): MotionEvent {
  return { id: type, type, title: type, description: '', level: 'info', timestamp: 0 }
}

describe('trajectory event marker filtering', () => {
  it('keeps pause, drop, flip and rotation annotations', () => {
    expect(isTrajectoryMarkerEvent(event('pause'))).toBe(true)
    expect(isTrajectoryMarkerEvent(event('drop'))).toBe(true)
    expect(isTrajectoryMarkerEvent(event('strong-shake'))).toBe(false)
    expect(isTrajectoryMarkerEvent(event('flip'))).toBe(true)
    expect(isTrajectoryMarkerEvent(event('rotation'))).toBe(true)
    expect(isTrajectoryMarkerEvent(event('moving'))).toBe(false)
    expect(isTrajectoryMarkerEvent(event('stationary'))).toBe(false)
  })

  it('requires a valid start and end for interval rendering', () => {
    expect(isTrajectoryIntervalEvent({ ...event('flip'), startTimestamp: 100, endTimestamp: 500 })).toBe(true)
    expect(isTrajectoryIntervalEvent({ ...event('rotation'), startTimestamp: 500, endTimestamp: 100 })).toBe(false)
    expect(isTrajectoryIntervalEvent(event('pause'))).toBe(false)
    expect(isTrajectoryIntervalEvent({ ...event('strong-shake'), startTimestamp: 0, endTimestamp: 100 })).toBe(false)
  })
})

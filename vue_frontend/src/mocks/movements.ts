import type { AnalysisMode, MovementDetail, MovementRecord } from '@/types/motion'
import {
  makeAccelerationSeries,
  makeGyroscopeSeries,
  makeMotionEvents,
  makeMovementRecord,
  makeOrientation,
  makeSessionTremorAverage,
  makeTrajectories,
} from './motion'

function makeMovementDetail(
  deviceId: number,
  movementId: number,
  analysisMode: AnalysisMode,
): MovementDetail {
  const record = makeMovementRecord(deviceId, movementId, analysisMode)
  const startedAt = Date.parse(record.startedAt)
  const sampleStepMs = record.sampleCount > 1
    ? record.durationSeconds * 1_000 / (record.sampleCount - 1)
    : 0
  const acceleration = makeAccelerationSeries(deviceId, record.sampleCount).map((point, index) => ({
    ...point,
    timestamp: Math.round(startedAt + index * sampleStepMs),
  }))
  const gyroscope = makeGyroscopeSeries(deviceId, record.sampleCount).map((point, index) => ({
    ...point,
    timestamp: acceleration[index].timestamp,
  }))
  const rawTrajectories = makeTrajectories(deviceId)
  const trajectories = {
    device: {
      ...rawTrajectories.device,
      points: rawTrajectories.device.points.map((point, index, points) => ({
        ...point,
        timestamp: Math.round(startedAt + index * record.durationSeconds * 1_000 / Math.max(1, points.length - 1)),
      })),
    },
    pcInertial: {
      ...rawTrajectories.pcInertial,
      points: rawTrajectories.pcInertial.points.map((point, index, points) => ({
        ...point,
        timestamp: Math.round(startedAt + index * record.durationSeconds * 1_000 / Math.max(1, points.length - 1)),
      })),
    },
  }
  const events = makeMotionEvents(deviceId).map((event, index, items) => ({
    ...event,
    id: `${deviceId}-${analysisMode}-${movementId}-event-${index + 1}`,
    timestamp: Math.round(startedAt + index * record.durationSeconds * 1_000 / Math.max(1, items.length)),
  }))
  return {
    ...record,
    acceleration,
    gyroscope,
    dynamicAcceleration: acceleration.map((point) => ({
      timestamp: point.timestamp,
      x: Number((point.x * 0.42).toFixed(4)),
      y: Number((point.y * 0.42).toFixed(4)),
      z: Number(((point.z - 9.81) * 0.42).toFixed(4)),
    })),
    speedSeries: acceleration.map((point, index) => ({
      timestamp: point.timestamp,
      value: Number((Math.abs(Math.sin((index + deviceId) / 13)) * record.speed.maximumMps).toFixed(4)),
    })),
    orientation: makeOrientation(deviceId),
    events,
    pauseEvents: [
      {
        timestamp: acceleration[Math.min(24, acceleration.length - 1)].timestamp,
        durationSeconds: 1.2,
        description: '移动过程中短暂停顿后继续',
      },
    ],
    dropEvents: deviceId === 4
      ? [
          {
            timestamp: acceleration[Math.min(56, acceleration.length - 1)].timestamp,
            durationSeconds: 0.18,
            description: '低重力区间后出现冲击峰值',
          },
        ]
      : [],
    trajectories,
    sessionTremorAverage: makeSessionTremorAverage(deviceId),
  }
}

export const mockMovementDetails: MovementDetail[] = [1, 2, 3, 4].flatMap((deviceId) => [
  makeMovementDetail(deviceId, 1, 'pc'),
  makeMovementDetail(deviceId, 2, 'pc'),
  makeMovementDetail(deviceId, 1, 'device'),
])

export const mockMovementRecords: MovementRecord[] = mockMovementDetails.map((detail) => ({
  movementId: detail.movementId,
  deviceId: detail.deviceId,
  analysisMode: detail.analysisMode,
  state: detail.state,
  startedAt: detail.startedAt,
  endedAt: detail.endedAt,
  durationSeconds: detail.durationSeconds,
  movingSeconds: detail.movingSeconds,
  sampleCount: detail.sampleCount,
  statistics: detail.statistics,
  speed: detail.speed,
  tremor: detail.tremor,
}))

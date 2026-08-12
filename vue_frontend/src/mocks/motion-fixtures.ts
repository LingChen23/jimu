import type {
  AnalysisMode,
  MotionSnapshot,
  RealtimeMotionData,
} from '@/types/motion'
import {
  makeMotionEvents,
  makeMotionStatistics,
  makeMovementRecord,
  makeOrientation,
  makeSessionTremorAverage,
  makeSpeedStatistics,
  makeTrajectories,
  makeTremorMetrics,
} from './motion-analysis'
import {
  MOCK_BASE_TIME_MS,
  makeAccelerationSeries,
  makeGyroscopeSeries,
} from './motion-series'

function makeSnapshot(deviceId: number): MotionSnapshot {
  const available = deviceId <= 4
  const motionState = available ? (deviceId === 2 || deviceId === 3 ? 'moving' : 'stationary') : 'unknown'
  const analysisMode: AnalysisMode = 'pc'
  const completedMovements = available
    ? [makeMovementRecord(deviceId, 1, analysisMode), makeMovementRecord(deviceId, 2, analysisMode)]
    : []
  return {
    deviceId,
    capturedAt: MOCK_BASE_TIME_MS + deviceId * 60_000 + 12_000,
    available,
    analysisMode,
    analysisModeLabel: 'PC 独立判定（推荐）',
    motionState,
    deviceReferenceState: available ? (motionState === 'moving' ? '设备报告移动' : '设备报告静止') : '等待设备',
    deviceModeState: available ? motionState : 'unknown',
    pcModeState: available ? motionState : 'unknown',
    statistics: makeMotionStatistics(deviceId, available),
    speed: makeSpeedStatistics(deviceId, available),
    tremor: makeTremorMetrics(deviceId, available),
    sessionTremorAverage: makeSessionTremorAverage(deviceId, available),
    orientation: makeOrientation(deviceId, available),
    trajectories: makeTrajectories(deviceId, available),
    currentMovement: deviceId === 3 ? makeMovementRecord(3, 3, analysisMode, 'active') : null,
    completedMovements,
    events: makeMotionEvents(deviceId, available),
  }
}

function makeRealtime(deviceId: number): RealtimeMotionData {
  const snapshot = makeSnapshot(deviceId)
  const acceleration = snapshot.available ? makeAccelerationSeries(deviceId) : []
  const gyroscope = snapshot.available ? makeGyroscopeSeries(deviceId) : []
  const latestAcceleration = acceleration.at(-1)
  const latestGyroscope = gyroscope.at(-1)
  return {
    deviceId,
    capturedAt: snapshot.capturedAt,
    available: snapshot.available,
    analysisMode: snapshot.analysisMode,
    motionState: snapshot.motionState,
    acceleration,
    gyroscope,
    latestAcceleration: latestAcceleration
      ? { x: latestAcceleration.x, y: latestAcceleration.y, z: latestAcceleration.z }
      : null,
    latestGyroscope: latestGyroscope
      ? { x: latestGyroscope.x, y: latestGyroscope.y, z: latestGyroscope.z }
      : null,
    orientation: snapshot.orientation,
    trajectories: snapshot.trajectories,
  }
}

export const mockMotionSnapshots: Record<number, MotionSnapshot> = Object.fromEntries(
  Array.from({ length: 12 }, (_, index) => {
    const deviceId = index + 1
    return [deviceId, makeSnapshot(deviceId)]
  }),
)

export const mockRealtimeData: Record<number, RealtimeMotionData> = Object.fromEntries(
  Array.from({ length: 12 }, (_, index) => {
    const deviceId = index + 1
    return [deviceId, makeRealtime(deviceId)]
  }),
)

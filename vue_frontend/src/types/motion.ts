export type AnalysisMode = 'pc' | 'device'
export type MotionState = 'stationary' | 'moving' | 'calibrating' | 'unknown'
export type MotionEventType =
  | 'moving'
  | 'stationary'
  | 'pause'
  | 'rotation'
  | 'flip'
  | 'strong-shake'
  | 'drop'

export interface Vector3 {
  x: number
  y: number
  z: number
}

export interface AxisPoint extends Vector3 {
  timestamp: number
}

export interface ScalarPoint {
  timestamp: number
  value: number
}

export interface MotionEvent {
  id: string
  deviceId?: number
  timestamp: number
  type: MotionEventType
  title: string
  description: string
  level: 'info' | 'warning' | 'danger'
}

export interface MotionStatistics {
  movingSeconds: number
  stationarySeconds: number
  pauseCount: number
  pauseTotalSeconds: number
  strongShakeCount: number
  flipCount: number
  rotationCount: number
  spatialRotationDegrees: number
  spatialRotationTurns: number
  dropCount: number
  suspectedDropCount: number
  abnormalCount: number
}

export interface SpeedStatistics {
  minimumMps?: number | null
  maximumMps: number
  p05Mps: number
  averageMps: number
  medianMps: number
  standardDeviationMps: number
  p05Percentile: 5
  sampleCount: number
}

export interface TremorMetrics {
  valid: boolean
  dominantFrequencyHz: number | null
  intensityDps: number | null
  bandRmsDps: number | null
  frequencyBandHz: readonly [number, number]
  reason: string | null
}

export interface SessionTremorAverage {
  validMovementCount: number
  totalMovementCount: number
  meanFrequencyHz: number | null
  meanIntensityDps: number | null
  aggregation: string
}

export interface Quaternion {
  w: number
  x: number
  y: number
  z: number
}

export interface OrientationSnapshot {
  initialized: boolean
  quaternion: Quaternion
  rollDegrees: number
  pitchDegrees: number
  yawDegrees: number
  yawReference: 'relative'
  positionM: Vector3
  velocityMps: Vector3
}

export interface TrajectoryPoint extends Vector3 {
  timestamp: number
}

export type DeviceTrajectory = {
  source: 'device'
  coordinateSystem: 'device-relative'
  unit: 'm'
  metricReliable: false
  points: TrajectoryPoint[]
}

export type PcInertialTrajectory = {
  source: 'pc-inertial'
  coordinateSystem: 'world-relative'
  unit: 'm'
  metricReliable: false
  points: TrajectoryPoint[]
}

export type MotionTrajectory = DeviceTrajectory | PcInertialTrajectory

export interface MovementRecord {
  movementId: number
  deviceId: number
  analysisMode: AnalysisMode
  state: 'active' | 'completed'
  startedAt: string
  endedAt: string | null
  durationSeconds: number
  movingSeconds: number
  sampleCount: number
  statistics: MotionStatistics
  speed: SpeedStatistics
  tremor: TremorMetrics
}

export interface TimedMotionEvent {
  timestamp: number
  durationSeconds: number
  description: string
}

export interface MovementDetail extends MovementRecord {
  acceleration: AxisPoint[]
  gyroscope: AxisPoint[]
  dynamicAcceleration: AxisPoint[]
  speedSeries: ScalarPoint[]
  orientation: OrientationSnapshot
  events: MotionEvent[]
  pauseEvents: TimedMotionEvent[]
  dropEvents: TimedMotionEvent[]
  trajectories: {
    device: DeviceTrajectory
    pcInertial: PcInertialTrajectory
  }
  sessionTremorAverage: SessionTremorAverage
}

export interface MotionSnapshot {
  deviceId: number
  capturedAt: number
  available: boolean
  analysisMode: AnalysisMode
  analysisModeLabel: string
  motionState: MotionState
  deviceReferenceState: string
  deviceModeState: MotionState
  pcModeState: MotionState
  statistics: MotionStatistics
  speed: SpeedStatistics
  tremor: TremorMetrics
  sessionTremorAverage: SessionTremorAverage
  orientation: OrientationSnapshot
  trajectoryRevision?: number
  trajectories: {
    device: DeviceTrajectory
    pcInertial: PcInertialTrajectory
  }
  currentMovement: MovementRecord | null
  completedMovements: MovementRecord[]
  events: MotionEvent[]
}

export interface RealtimeMotionData {
  deviceId: number
  capturedAt: number
  available: boolean
  analysisMode: AnalysisMode
  motionState: MotionState
  acceleration: AxisPoint[]
  gyroscope: AxisPoint[]
  latestAcceleration: Vector3 | null
  latestGyroscope: Vector3 | null
  orientation: OrientationSnapshot
  trajectoryRevision?: number
  movementId?: number | null
  trajectories: {
    device: DeviceTrajectory
    pcInertial: PcInertialTrajectory
  }
}

export type MovementExportFormat = 'csv' | 'json'

export interface MovementExport {
  fileName: string
  format: MovementExportFormat
  mimeType: 'text/csv;charset=utf-8' | 'application/json;charset=utf-8'
  content: string
}

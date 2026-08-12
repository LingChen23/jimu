import type { AxisPoint, MotionEvent, MotionStatistics } from './motion'
import type { AnalysisMode } from './motion'

export type TaskStatus = 'collecting' | 'completed' | 'abnormal' | 'cancelled'
export type DataQuality = 'complete' | 'good' | 'normal' | 'poor'

export interface TaskMovementReference {
  deviceId: number
  movementId: number
  analysisMode: AnalysisMode
}

export interface TaskSummary {
  taskId: string
  participantId: string
  blockId: string
  deviceId: number
  deviceIds: number[]
  taskType: string
  note?: string
  startTime: string
  endTime: string | null
  durationSeconds: number | null
  totalScore: number | null
  dataQuality: DataQuality
  status: TaskStatus
  movementReferences?: TaskMovementReference[]
}

export interface TaskResult {
  task: TaskSummary
  movementReference?: TaskMovementReference | null
  score: {
    totalScore: number | null
    scoreStatus: 'pending' | 'processing' | 'completed' | 'failed'
    modelVersion: string | null
    explanation: string[]
    demoOnly: boolean
  }
  statistics: MotionStatistics
  acceleration: AxisPoint[]
  gyroscope: AxisPoint[]
  events: MotionEvent[]
}

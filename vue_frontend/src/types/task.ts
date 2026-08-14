import type { AxisPoint, MotionEvent, MotionStatistics } from './motion'
import type { AnalysisMode } from './motion'

export type TaskStatus = 'collecting' | 'completed' | 'abnormal' | 'cancelled'
export type DataQuality = 'complete' | 'good' | 'normal' | 'poor'

export interface ParticipantProfile {
  participantId: string
  name: string
  gender: string
  age: number | null
  birthDate: string
  educationLevel: string
  dominantHand: string
  phone: string
  visionStatus: string
  hearingStatus: string
  upperLimbStatus: string
  cognitiveStatus: string
  profileNote: string
}

export interface TaskMovementReference {
  deviceId: number
  blockId?: string
  movementId: number
  analysisMode: AnalysisMode
  startedAt?: string | null
  endedAt?: string | null
  durationSeconds?: number | null
  taskMovementIndex?: number
  deviceMovementIndex?: number
  score?: number | null
  scoreModelVersion?: string | null
}

export interface TaskBlockBinding {
  deviceId: number
  blockId: string
}

export interface TaskSummary {
  taskId: string
  participantId: string
  participantSnapshot?: ParticipantProfile | null
  blockId: string
  deviceId: number
  deviceIds?: number[]
  blockBindings?: TaskBlockBinding[]
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

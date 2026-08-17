import type { BlockMapping, DeviceSnapshot } from '@/types/device'
import type {
  AnalysisMode,
  MotionSnapshot,
  MovementDetail,
  MovementExport,
  MovementExportFormat,
  RealtimeMotionData,
} from '@/types/motion'
import type {
  BatteryRefreshResult,
  DeviceCommand,
  DeviceCommandResult,
  LinkDiagnostics,
  SerialPortInfo,
  SerialStatus,
} from '@/types/serial'
import type { ParticipantProfile, TaskArchiveExport, TaskResult, TaskSummary } from '@/types/task'

export interface DeviceRequest {
  deviceId: number
}

export interface RealtimeDataRequest extends DeviceRequest {
  trajectoryRevision?: number
  deviceTrajectoryCursor?: number
  pcTrajectoryCursor?: number
}

export interface OpenSerialRequest {
  port: string
  baudRate: number
}

export interface DeviceCommandRequest extends DeviceRequest {
  command: DeviceCommand
}

export interface MovementDetailRequest extends DeviceRequest {
  movementId: number
  analysisMode?: AnalysisMode
  taskId?: string
}

export interface MovementExportRequest extends MovementDetailRequest {
  format: MovementExportFormat
}

export interface TaskResultRequest {
  taskId: string
}

export interface StartTaskRequest extends DeviceRequest {
  participantId: string
  participantProfile: ParticipantProfile
  note: string
}

export interface FinishTaskRequest {
  taskId: string
}

export interface DeleteTaskRequest {
  taskId: string
}

export interface MotionDataService {
  listSerialPorts(): Promise<SerialPortInfo[]>
  getSerialStatus(): Promise<SerialStatus>
  openSerial(request: OpenSerialRequest): Promise<SerialStatus>
  closeSerial(): Promise<SerialStatus>
  resetTransportCounters(): Promise<SerialStatus>

  listDevices(): Promise<DeviceSnapshot[]>
  getBlockMapping(): Promise<BlockMapping>
  updateBlockMapping(mapping: BlockMapping): Promise<BlockMapping>
  getMotionSnapshot(request: DeviceRequest): Promise<MotionSnapshot>
  getRealtimeData(request: RealtimeDataRequest): Promise<RealtimeMotionData>
  getLinkDiagnostics(request: DeviceRequest): Promise<LinkDiagnostics>

  resetDataAndErrorCounters(request: DeviceRequest): Promise<DeviceSnapshot>
  executeDeviceCommand(request: DeviceCommandRequest): Promise<DeviceCommandResult>
  refreshBatteryVoltages(): Promise<BatteryRefreshResult>

  getMovementDetail(request: MovementDetailRequest): Promise<MovementDetail>
  exportMovement(request: MovementExportRequest): Promise<MovementExport>
  exportTask(request: TaskResultRequest): Promise<TaskArchiveExport>

  getCurrentTask(request: DeviceRequest): Promise<TaskSummary | null>
  listParticipants(): Promise<ParticipantProfile[]>
  startTask(request: StartTaskRequest): Promise<TaskSummary>
  finishTask(request: FinishTaskRequest): Promise<TaskSummary>
  deleteTask(request: DeleteTaskRequest): Promise<TaskSummary>
  listTasks(): Promise<TaskSummary[]>
  getTaskResult(request: TaskResultRequest): Promise<TaskResult>
  getTaskMovementDetail(request: MovementDetailRequest & { taskId: string }): Promise<MovementDetail>
}

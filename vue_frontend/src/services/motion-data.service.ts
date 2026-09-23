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
import type {
  CloudUploadSnapshot,
  ParticipantProfile,
  TaskArchiveExport,
  TaskResult,
  TaskSummary,
} from '@/types/task'

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

export interface TaskBatchExportRequest {
  taskIds: string[]
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

export interface CloudTaskPage {
  tasks: TaskSummary[]
  total: number
  page: number
  pageSize: number
}

export interface UpdateTaskParticipantRequest extends TaskResultRequest {
  participantProfile: ParticipantProfile
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
  exportTasks(request: TaskBatchExportRequest): Promise<TaskArchiveExport>

  getCurrentTask(request: DeviceRequest): Promise<TaskSummary | null>
  listParticipants(): Promise<ParticipantProfile[]>
  startTask(request: StartTaskRequest): Promise<TaskSummary>
  finishTask(request: FinishTaskRequest): Promise<TaskSummary>
  updateTaskParticipant(request: UpdateTaskParticipantRequest): Promise<TaskSummary>
  deleteTask(request: DeleteTaskRequest): Promise<TaskSummary>
  listTasks(): Promise<TaskSummary[]>
  listCloudTasks(page?: number, pageSize?: number): Promise<CloudTaskPage>
  getTaskResult(request: TaskResultRequest): Promise<TaskResult>
  getTaskMovementDetail(request: MovementDetailRequest & { taskId: string }): Promise<MovementDetail>
  getCloudUploadStatus(): Promise<CloudUploadSnapshot>
  enqueueCloudUpload(taskIds: string[]): Promise<void>
}

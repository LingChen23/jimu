import type { BlockMapping, DeviceSnapshot } from '@/types/device'
import type {
  AnalysisMode,
  MotionSnapshot,
  MovementDetail,
  MovementExport,
  MovementExportFormat,
  MovementRecord,
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
import type { TaskResult, TaskSummary } from '@/types/task'

export interface DeviceRequest {
  deviceId: number
}

export interface OpenSerialRequest {
  port: string
  baudRate: number
}

export interface SetAnalysisModeRequest extends DeviceRequest {
  analysisMode: AnalysisMode
}

export interface DeviceCommandRequest extends DeviceRequest {
  command: DeviceCommand
}

export interface MovementListRequest extends DeviceRequest {
  analysisMode?: AnalysisMode
}

export interface MovementDetailRequest extends MovementListRequest {
  movementId: number
}

export interface MovementExportRequest extends MovementDetailRequest {
  format: MovementExportFormat
}

export interface TaskResultRequest {
  taskId: string
}

export interface StartTaskRequest extends DeviceRequest {
  participantId: string
  note: string
}

export interface FinishTaskRequest {
  taskId: string
}

export interface MotionDataService {
  listSerialPorts(): Promise<SerialPortInfo[]>
  getSerialStatus(): Promise<SerialStatus>
  openSerial(request: OpenSerialRequest): Promise<SerialStatus>
  closeSerial(): Promise<SerialStatus>

  listDevices(): Promise<DeviceSnapshot[]>
  getBlockMapping(): Promise<BlockMapping>
  updateBlockMapping(mapping: BlockMapping): Promise<BlockMapping>
  getMotionSnapshot(request: DeviceRequest): Promise<MotionSnapshot>
  getRealtimeData(request: DeviceRequest): Promise<RealtimeMotionData>
  getLinkDiagnostics(request: DeviceRequest): Promise<LinkDiagnostics>

  setAnalysisMode(request: SetAnalysisModeRequest): Promise<MotionSnapshot>
  resetAnalysis(request: DeviceRequest): Promise<MotionSnapshot>
  resetPacketCounters(request: DeviceRequest): Promise<DeviceSnapshot>
  resetDataAndErrorCounters(request: DeviceRequest): Promise<DeviceSnapshot>
  executeDeviceCommand(request: DeviceCommandRequest): Promise<DeviceCommandResult>
  refreshBatteryVoltages(): Promise<BatteryRefreshResult>

  listMovements(request: MovementListRequest): Promise<MovementRecord[]>
  getMovementDetail(request: MovementDetailRequest): Promise<MovementDetail>
  exportMovement(request: MovementExportRequest): Promise<MovementExport>

  getCurrentTask(request: DeviceRequest): Promise<TaskSummary | null>
  startTask(request: StartTaskRequest): Promise<TaskSummary>
  finishTask(request: FinishTaskRequest): Promise<TaskSummary>
  listTasks(): Promise<TaskSummary[]>
  getTaskResult(request: TaskResultRequest): Promise<TaskResult>
}

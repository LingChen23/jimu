import type { BlockMapping, DeviceSnapshot } from '@/types/device'
import type {
  MotionSnapshot,
  MovementDetail,
  MovementExport,
  MovementRecord,
  RealtimeMotionData,
} from '@/types/motion'
import type {
  BatteryRefreshResult,
  DeviceCommandResult,
  LinkDiagnostics,
  SerialPortInfo,
  SerialStatus,
} from '@/types/serial'
import type { TaskResult, TaskSummary } from '@/types/task'
import type {
  DeviceCommandRequest,
  DeviceRequest,
  FinishTaskRequest,
  MotionDataService,
  MovementDetailRequest,
  MovementExportRequest,
  MovementListRequest,
  OpenSerialRequest,
  SetAnalysisModeRequest,
  StartTaskRequest,
  TaskResultRequest,
} from './motion-data.service'
import {
  executeMockDeviceCommand,
  getMockLinkDiagnostics,
  getMockMotionSnapshot,
  getMockRealtimeData,
  listMockDevices,
  resetMockAnalysis,
  resetMockPacketCounters,
  setMockAnalysisMode,
} from './mock/device.mock-api'
import {
  exportMockMovement,
  getMockMovementDetail,
  listMockMovements,
} from './mock/movement.mock-api'
import { createMockServiceState } from './mock/mock-service-state'
import {
  closeMockSerial,
  getMockSerialStatus,
  listMockSerialPorts,
  openMockSerial,
} from './mock/serial.mock-api'
import {
  getMockCurrentTask,
  getMockTaskResult,
  listMockTasks,
} from './mock/task.mock-api'

export class MockMotionDataService implements MotionDataService {
  private readonly state = createMockServiceState()
  private activeTask: TaskSummary | null = null
  private readonly createdTasks: TaskSummary[] = []

  async listSerialPorts(): Promise<SerialPortInfo[]> {
    return listMockSerialPorts()
  }

  async getSerialStatus(): Promise<SerialStatus> {
    return getMockSerialStatus(this.state)
  }

  async openSerial(request: OpenSerialRequest): Promise<SerialStatus> {
    return openMockSerial(this.state, request)
  }

  async closeSerial(): Promise<SerialStatus> {
    return closeMockSerial(this.state)
  }

  async listDevices(): Promise<DeviceSnapshot[]> {
    return listMockDevices(this.state)
  }

  async getBlockMapping(): Promise<BlockMapping> {
    return {
      mappingVersion: 'mock-a-to-l-v1',
      editableSource: null,
      devices: this.state.devices.map((device) => ({
        deviceId: device.deviceId,
        blockId: device.blockId,
      })),
    }
  }

  async updateBlockMapping(mapping: BlockMapping): Promise<BlockMapping> {
    for (const entry of mapping.devices) {
      const device = this.state.devices.find((item) => item.deviceId === entry.deviceId)
      if (device) device.blockId = entry.blockId
    }
    return structuredClone(mapping)
  }

  async getMotionSnapshot(request: DeviceRequest): Promise<MotionSnapshot> {
    return getMockMotionSnapshot(this.state, request)
  }

  async getRealtimeData(request: DeviceRequest): Promise<RealtimeMotionData> {
    return getMockRealtimeData(this.state, request)
  }

  async getLinkDiagnostics(request: DeviceRequest): Promise<LinkDiagnostics> {
    return getMockLinkDiagnostics(this.state, request)
  }

  async setAnalysisMode(request: SetAnalysisModeRequest): Promise<MotionSnapshot> {
    return setMockAnalysisMode(this.state, request)
  }

  async resetAnalysis(request: DeviceRequest): Promise<MotionSnapshot> {
    return resetMockAnalysis(this.state, request)
  }

  async resetPacketCounters(request: DeviceRequest): Promise<DeviceSnapshot> {
    return resetMockPacketCounters(this.state, request)
  }

  async resetDataAndErrorCounters(request: DeviceRequest): Promise<DeviceSnapshot> {
    const device = resetMockPacketCounters(this.state, request)
    const source = this.state.devices.find((item) => item.deviceId === request.deviceId)!
    source.health.errors = {
      sampleGapCount: 0,
      sensorErrorCount: 0,
      queueDropCount: 0,
      bleResourceWaitCount: 0,
      abnormalCount: 0,
    }
    return structuredClone({ ...device, health: source.health })
  }

  async executeDeviceCommand(request: DeviceCommandRequest): Promise<DeviceCommandResult> {
    return executeMockDeviceCommand(this.state, request)
  }

  async refreshBatteryVoltages(): Promise<BatteryRefreshResult> {
    if (!this.state.serial.isOpen) throw new Error('串口未打开')
    const results = this.state.devices.map((device) => ({
      deviceId: device.deviceId,
      ok: device.connectionStatus === 'online' && device.health.batteryVoltageMv !== null,
      batteryVoltageMv: device.health.batteryVoltageMv,
      batteryLow: device.health.batteryLow,
      message: device.connectionStatus === 'online' ? '获取成功' : '设备无响应，已跳过',
    }))
    const succeeded = results.filter((item) => item.ok).length
    return { requested: results.length, succeeded, skipped: results.length - succeeded, results }
  }

  async listMovements(request: MovementListRequest): Promise<MovementRecord[]> {
    return listMockMovements(this.state, request)
  }

  async getMovementDetail(request: MovementDetailRequest): Promise<MovementDetail> {
    return getMockMovementDetail(this.state, request)
  }

  async exportMovement(request: MovementExportRequest): Promise<MovementExport> {
    return exportMockMovement(this.state, request)
  }

  async getCurrentTask(request: DeviceRequest): Promise<TaskSummary | null> {
    if (this.activeTask?.deviceId === request.deviceId) return structuredClone(this.activeTask)
    return getMockCurrentTask(this.state, request)
  }

  async startTask(request: StartTaskRequest): Promise<TaskSummary> {
    const device = this.state.devices.find((item) => item.deviceId === request.deviceId)
    if (!device) throw new Error(`未知设备ID: ${request.deviceId}`)
    const now = new Date()
    this.activeTask = {
      taskId: `T${now.getTime()}`,
      participantId: request.participantId,
      blockId: device.blockId,
      deviceId: device.deviceId,
      deviceIds: [device.deviceId],
      taskType: request.note,
      note: request.note,
      startTime: now.toISOString(),
      endTime: null,
      durationSeconds: 0,
      totalScore: null,
      dataQuality: 'normal',
      status: 'collecting',
      movementReferences: [],
    }
    this.createdTasks.unshift(this.activeTask)
    return structuredClone(this.activeTask)
  }

  async finishTask(request: FinishTaskRequest): Promise<TaskSummary> {
    if (!this.activeTask || this.activeTask.taskId !== request.taskId) throw new Error('未找到采集任务')
    const end = new Date()
    this.activeTask.endTime = end.toISOString()
    this.activeTask.durationSeconds = Math.max(0, Math.round((end.getTime() - Date.parse(this.activeTask.startTime)) / 1000))
    this.activeTask.status = 'completed'
    const result = structuredClone(this.activeTask)
    this.activeTask = null
    return result
  }

  async listTasks(): Promise<TaskSummary[]> {
    return [...structuredClone(this.createdTasks), ...listMockTasks()]
  }

  async getTaskResult(request: TaskResultRequest): Promise<TaskResult> {
    return getMockTaskResult(request)
  }
}

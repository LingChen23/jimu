import { MOCK_BASE_TIME_MS, makeMotionStatistics } from '@/mocks'
import type { DeviceSnapshot } from '@/types/device'
import type { MotionSnapshot, RealtimeMotionData } from '@/types/motion'
import type { DeviceCommandResult, LinkDiagnostics } from '@/types/serial'
import type {
  DeviceCommandRequest,
  DeviceRequest,
  SetAnalysisModeRequest,
} from '../motion-data.service'
import {
  cloneMock,
  requireMockDevice,
  type MockServiceState,
} from './mock-service-state'

function commandResult(
  state: MockServiceState,
  request: DeviceCommandRequest,
  ok: boolean,
  message: string,
  failureCode: DeviceCommandResult['failureCode'],
  data: DeviceCommandResult['data'] = null,
): DeviceCommandResult {
  state.requestId += 1
  return {
    ok,
    deviceId: request.deviceId,
    command: request.command,
    requestId: state.requestId,
    resultCode: ok ? 0 : 1,
    failureCode,
    message,
    executedAt: new Date(MOCK_BASE_TIME_MS + state.requestId * 1_000).toISOString(),
    data: cloneMock(data),
  }
}

export function listMockDevices(state: MockServiceState): DeviceSnapshot[] {
  return cloneMock(state.devices)
}

export function getMockMotionSnapshot(state: MockServiceState, request: DeviceRequest): MotionSnapshot {
  requireMockDevice(state, request.deviceId)
  return cloneMock(state.snapshots[request.deviceId])
}

export function getMockRealtimeData(state: MockServiceState, request: DeviceRequest): RealtimeMotionData {
  requireMockDevice(state, request.deviceId)
  return cloneMock(state.realtime[request.deviceId])
}

export function getMockLinkDiagnostics(state: MockServiceState, request: DeviceRequest): LinkDiagnostics {
  requireMockDevice(state, request.deviceId)
  return cloneMock(state.links[request.deviceId])
}

export function setMockAnalysisMode(
  state: MockServiceState,
  request: SetAnalysisModeRequest,
): MotionSnapshot {
  requireMockDevice(state, request.deviceId)
  const snapshot = state.snapshots[request.deviceId]
  snapshot.analysisMode = request.analysisMode
  snapshot.analysisModeLabel = request.analysisMode === 'pc'
    ? 'PC 独立判定（推荐）'
    : '设备状态驱动（原方案）'
  state.realtime[request.deviceId].analysisMode = request.analysisMode
  return cloneMock(snapshot)
}

export function resetMockAnalysis(state: MockServiceState, request: DeviceRequest): MotionSnapshot {
  requireMockDevice(state, request.deviceId)
  const snapshot = state.snapshots[request.deviceId]
  snapshot.statistics = makeMotionStatistics(request.deviceId, false)
  snapshot.currentMovement = null
  snapshot.completedMovements = []
  snapshot.events = []
  snapshot.sessionTremorAverage = {
    validMovementCount: 0,
    totalMovementCount: 0,
    meanFrequencyHz: null,
    meanIntensityDps: null,
    aggregation: '已完成移动的有效FFT主频和强度算术平均',
  }
  return cloneMock(snapshot)
}

export function resetMockPacketCounters(
  state: MockServiceState,
  request: DeviceRequest,
): DeviceSnapshot {
  const device = requireMockDevice(state, request.deviceId)
  device.packetCount = 0
  device.lostPacketCount = 0
  const link = state.links[request.deviceId]
  link.receivedFrames = 0
  link.sequenceGapFrames = 0
  return cloneMock(device)
}

export function executeMockDeviceCommand(
  state: MockServiceState,
  request: DeviceCommandRequest,
): DeviceCommandResult {
  const device = requireMockDevice(state, request.deviceId)
  if (device.integrationStatus === 'pending-integration') {
    return commandResult(state, request, false, '设备待接入，无法执行命令', 'pending-integration')
  }
  if (!state.serial.isOpen) {
    return commandResult(state, request, false, '串口未打开', 'serial-closed')
  }

  let data: DeviceCommandResult['data'] = null
  if (request.command === 'GET_INFO') data = device.health.firmware
  if (request.command === 'GET_STATUS') data = device.health
  if (request.command === 'START_STREAM') {
    device.usageStatus = 'collecting'
    device.health.streaming = true
  }
  if (request.command === 'STOP_STREAM') {
    device.usageStatus = 'idle'
    device.health.streaming = false
  }
  if (request.command === 'RESET_STATS') resetMockPacketCounters(state, request)
  if (request.command === 'RECALIBRATE') {
    device.motionState = 'calibrating'
    device.health.calibrated = true
  }
  return commandResult(state, request, true, '模拟命令执行成功', null, data)
}

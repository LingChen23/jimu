import { mockSerialPorts } from '@/mocks'
import type { SerialPortInfo, SerialStatus } from '@/types/serial'
import type { OpenSerialRequest } from '../motion-data.service'
import { cloneMock, type MockServiceState } from './mock-service-state'

export function listMockSerialPorts(): SerialPortInfo[] {
  return cloneMock(mockSerialPorts)
}

export function getMockSerialStatus(state: MockServiceState): SerialStatus {
  return cloneMock(state.serial)
}

export function openMockSerial(state: MockServiceState, request: OpenSerialRequest): SerialStatus {
  if (!mockSerialPorts.some((item) => item.port === request.port)) {
    throw new Error(`未知串口: ${request.port}`)
  }
  if (!Number.isFinite(request.baudRate) || request.baudRate <= 0) {
    throw new Error('波特率必须大于0')
  }
  state.serial = {
    ...state.serial,
    isOpen: true,
    port: request.port,
    baudRate: request.baudRate,
    receiveStatus: 'receiving',
    errorMessage: null,
    byteCount: 128_640,
    frameCount: 1_286,
    encodedFrameCount: 1_286,
    gatewayLostFrames: 2,
  }
  return cloneMock(state.serial)
}

export function closeMockSerial(state: MockServiceState): SerialStatus {
  state.serial = {
    ...state.serial,
    isOpen: false,
    port: null,
    receiveStatus: 'idle',
    errorMessage: null,
  }
  return cloneMock(state.serial)
}

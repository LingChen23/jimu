import {
  mockDevices,
  mockLinkDiagnostics,
  mockMotionSnapshots,
  mockRealtimeData,
  mockSerialStatus,
} from '@/mocks'
import type { DeviceSnapshot } from '@/types/device'
import type { MotionSnapshot, RealtimeMotionData } from '@/types/motion'
import type { LinkDiagnostics, SerialStatus } from '@/types/serial'

export interface MockServiceState {
  devices: DeviceSnapshot[]
  snapshots: Record<number, MotionSnapshot>
  realtime: Record<number, RealtimeMotionData>
  links: Record<number, LinkDiagnostics>
  serial: SerialStatus
  requestId: number
}

export function cloneMock<T>(value: T): T {
  return structuredClone(value)
}

export function createMockServiceState(): MockServiceState {
  return {
    devices: cloneMock(mockDevices),
    snapshots: cloneMock(mockMotionSnapshots),
    realtime: cloneMock(mockRealtimeData),
    links: cloneMock(mockLinkDiagnostics),
    serial: cloneMock(mockSerialStatus),
    requestId: 0,
  }
}

export function requireMockDevice(state: MockServiceState, deviceId: number): DeviceSnapshot {
  const device = state.devices.find((item) => item.deviceId === deviceId)
  if (!device) throw new Error(`未知设备ID: ${deviceId}`)
  return device
}

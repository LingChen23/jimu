import { mockMovementDetails, mockMovementRecords } from '@/mocks'
import type {
  MovementDetail,
  MovementExport,
  MovementRecord,
} from '@/types/motion'
import type {
  MovementDetailRequest,
  MovementExportRequest,
  MovementListRequest,
} from '../motion-data.service'
import {
  cloneMock,
  requireMockDevice,
  type MockServiceState,
} from './mock-service-state'

export function listMockMovements(
  state: MockServiceState,
  request: MovementListRequest,
): MovementRecord[] {
  const device = requireMockDevice(state, request.deviceId)
  if (device.integrationStatus === 'pending-integration') return []
  return cloneMock(mockMovementRecords.filter((item) => (
    item.deviceId === request.deviceId
    && (!request.analysisMode || item.analysisMode === request.analysisMode)
  )))
}

export function getMockMovementDetail(
  state: MockServiceState,
  request: MovementDetailRequest,
): MovementDetail {
  requireMockDevice(state, request.deviceId)
  const analysisMode = request.analysisMode ?? 'pc'
  const detail = mockMovementDetails.find((item) => (
    item.deviceId === request.deviceId
    && item.movementId === request.movementId
    && item.analysisMode === analysisMode
  ))
  if (!detail) throw new Error('未找到该移动记录')
  return cloneMock(detail)
}

export function exportMockMovement(
  state: MockServiceState,
  request: MovementExportRequest,
): MovementExport {
  const detail = getMockMovementDetail(state, request)
  if (request.format === 'json') {
    return {
      fileName: `device_${request.deviceId}_movement_${request.movementId}_${detail.analysisMode}.json`,
      format: 'json',
      mimeType: 'application/json;charset=utf-8',
      content: JSON.stringify(detail, null, 2),
    }
  }

  const rows = [
    'timestamp,accel_x_mps2,accel_y_mps2,accel_z_mps2,speed_mps',
    ...detail.acceleration.map((point, index) => [
      point.timestamp,
      point.x,
      point.y,
      point.z,
      detail.speedSeries[index]?.value ?? 0,
    ].join(',')),
  ]
  return {
    fileName: `device_${request.deviceId}_movement_${request.movementId}_${detail.analysisMode}.csv`,
    format: 'csv',
    mimeType: 'text/csv;charset=utf-8',
    content: rows.join('\n'),
  }
}

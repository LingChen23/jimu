import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { motionDataService } from '@/services/data.service'
import type { BlockMapping, DeviceSnapshot } from '@/types/device'
import type {
  AnalysisMode,
  MotionSnapshot,
  MovementDetail,
  MovementExport,
  MovementExportFormat,
  MotionTrajectory,
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

export function mergeLiveTrajectory<T extends MotionTrajectory>(
  previous: T | undefined,
  incoming: T,
  reset: boolean,
): T {
  if (reset || !previous) return incoming
  if (!incoming.points.length) return { ...incoming, points: previous.points } as T
  const merged = [...previous.points]
  for (const point of incoming.points) {
    const prior = merged[merged.length - 1]
    if (!prior || prior.x !== point.x || prior.y !== point.y || prior.z !== point.z) {
      merged.push(point)
    }
  }
  return { ...incoming, points: merged.slice(-4_000) } as T
}

export const useMotionStore = defineStore('motion', () => {
  const devices = ref<DeviceSnapshot[]>([])
  const serialPorts = ref<SerialPortInfo[]>([])
  const serialStatus = ref<SerialStatus | null>(null)
  const blockMapping = ref<BlockMapping | null>(null)
  const snapshots = ref<Record<number, MotionSnapshot>>({})
  const realtimeData = ref<Record<number, RealtimeMotionData>>({})
  const linkDiagnostics = ref<Record<number, LinkDiagnostics>>({})
  const movementDetails = ref<Record<string, MovementDetail>>({})
  const controlResults = ref<Record<number, DeviceCommandResult>>({})

  const onlineCount = computed(() => devices.value.filter((device) => device.integrationStatus === 'tested' && device.connectionStatus === 'online').length)
  const offlineCount = computed(() => devices.value.filter((device) => device.integrationStatus === 'tested' && device.connectionStatus === 'offline').length)
  const warningCount = computed(() => devices.value.filter((device) => device.integrationStatus === 'tested' && device.connectionStatus === 'warning').length)

  function deviceById(deviceId: number): DeviceSnapshot | null {
    return devices.value.find((device) => device.deviceId === deviceId) ?? null
  }

  async function loadDevices(): Promise<void> {
    devices.value = await motionDataService.listDevices()
  }

  async function loadBlockMapping(): Promise<void> {
    blockMapping.value = await motionDataService.getBlockMapping()
  }

  async function updateBlockMapping(mapping: BlockMapping): Promise<void> {
    blockMapping.value = await motionDataService.updateBlockMapping(mapping)
    devices.value = await motionDataService.listDevices()
  }

  async function loadSerial(): Promise<void> {
    const [ports, status] = await Promise.all([
      motionDataService.listSerialPorts(),
      motionDataService.getSerialStatus(),
    ])
    serialPorts.value = ports
    serialStatus.value = status
  }

  async function refreshDeviceStatus(): Promise<void> {
    // Background status polling intentionally skips `run()` so it does not
    // toggle the page-wide loading state on every refresh.
    const [status, nextDevices] = await Promise.all([
      motionDataService.getSerialStatus(),
      motionDataService.listDevices(),
    ])
    serialStatus.value = status
    devices.value = nextDevices
  }

  async function openSerial(port: string, baudRate: number): Promise<void> {
    serialStatus.value = await motionDataService.openSerial({ port, baudRate })
  }

  async function closeSerial(): Promise<void> {
    serialStatus.value = await motionDataService.closeSerial()
  }

  async function resetTransportCounters(): Promise<void> {
    serialStatus.value = await motionDataService.resetTransportCounters()
  }

  async function loadDeviceContext(deviceId: number): Promise<void> {
    const [snapshot, realtime, diagnostics] = await Promise.all([
      motionDataService.getMotionSnapshot({ deviceId }),
      motionDataService.getRealtimeData({ deviceId }),
      motionDataService.getLinkDiagnostics({ deviceId }),
    ])
    snapshots.value[deviceId] = snapshot
    realtimeData.value[deviceId] = {
      ...realtime,
      trajectories: snapshot.trajectories,
    }
    linkDiagnostics.value[deviceId] = diagnostics
  }

  async function loadRealtimeContext(deviceId: number): Promise<void> {
    const [snapshot, realtime] = await Promise.all([
      motionDataService.getMotionSnapshot({ deviceId }),
      motionDataService.getRealtimeData({ deviceId }),
    ])
    snapshots.value[deviceId] = snapshot
    realtimeData.value[deviceId] = {
      ...realtime,
      trajectories: snapshot.trajectories,
    }
  }

  async function refreshRealtimeData(deviceId: number): Promise<void> {
    const previous = realtimeData.value[deviceId]
    const incoming = await motionDataService.getRealtimeData({
      deviceId,
      trajectoryRevision: previous?.trajectoryRevision,
      deviceTrajectoryCursor: previous?.deviceTrajectoryCursor,
      pcTrajectoryCursor: previous?.pcTrajectoryCursor,
    })
    const reset = Boolean(
      incoming.trajectoryReset
      || incoming.deviceTrajectoryTruncated
      || incoming.pcTrajectoryTruncated
      || previous?.trajectoryRevision !== incoming.trajectoryRevision,
    )
    realtimeData.value[deviceId] = {
      ...incoming,
      trajectories: {
        device: mergeLiveTrajectory(previous?.trajectories.device, incoming.trajectories.device, reset),
        pcInertial: mergeLiveTrajectory(previous?.trajectories.pcInertial, incoming.trajectories.pcInertial, reset),
      },
    }
  }

  async function resetDataAndErrorCounters(deviceId: number): Promise<void> {
    const updated = await motionDataService.resetDataAndErrorCounters({ deviceId })
    const index = devices.value.findIndex((device) => device.deviceId === deviceId)
    if (index >= 0) devices.value[index] = updated
  }

  async function executeCommand(deviceId: number, command: DeviceCommand): Promise<DeviceCommandResult> {
    const result = await motionDataService.executeDeviceCommand({ deviceId, command })
    controlResults.value[deviceId] = result
    devices.value = await motionDataService.listDevices()
    return result
  }

  async function refreshBatteryVoltages(): Promise<BatteryRefreshResult> {
    const result = await motionDataService.refreshBatteryVoltages()
    devices.value = await motionDataService.listDevices()
    return result
  }

  async function loadMovementDetail(
    deviceId: number,
    movementId: number,
    analysisMode?: AnalysisMode,
    taskId?: string,
  ): Promise<MovementDetail> {
    const request = { deviceId, movementId, analysisMode }
    const detail = await (taskId
      ? motionDataService.getTaskMovementDetail({ ...request, taskId })
      : motionDataService.getMovementDetail(request))
    movementDetails.value[`${taskId ?? 'live'}:${deviceId}:${analysisMode ?? 'pc'}:${movementId}`] = detail
    return detail
  }

  function movementDetailByKey(
    deviceId: number,
    movementId: number,
    analysisMode: AnalysisMode = 'pc',
    taskId?: string,
  ): MovementDetail | null {
    return movementDetails.value[`${taskId ?? 'live'}:${deviceId}:${analysisMode}:${movementId}`] ?? null
  }

  async function exportMovement(
    deviceId: number,
    movementId: number,
    format: MovementExportFormat,
    analysisMode?: AnalysisMode,
    taskId?: string,
  ): Promise<MovementExport> {
    return motionDataService.exportMovement({ deviceId, movementId, analysisMode, format, taskId })
  }

  return {
    devices,
    serialPorts,
    serialStatus,
    blockMapping,
    snapshots,
    realtimeData,
    linkDiagnostics,
    movementDetails,
    controlResults,
    onlineCount,
    offlineCount,
    warningCount,
    deviceById,
    loadDevices,
    loadBlockMapping,
    updateBlockMapping,
    loadSerial,
    refreshDeviceStatus,
    openSerial,
    closeSerial,
    resetTransportCounters,
    loadDeviceContext,
    loadRealtimeContext,
    refreshRealtimeData,
    resetDataAndErrorCounters,
    executeCommand,
    refreshBatteryVoltages,
    loadMovementDetail,
    movementDetailByKey,
    exportMovement,
  }
})

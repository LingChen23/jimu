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
  MovementRecord,
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
  const latest = incoming.points[incoming.points.length - 1]
  if (!latest) return { ...incoming, points: previous.points } as T
  const prior = previous.points[previous.points.length - 1]
  if (prior && prior.x === latest.x && prior.y === latest.y && prior.z === latest.z) {
    return { ...incoming, points: previous.points } as T
  }
  return { ...incoming, points: [...previous.points, latest].slice(-4_000) } as T
}

export const useMotionStore = defineStore('motion', () => {
  const devices = ref<DeviceSnapshot[]>([])
  const serialPorts = ref<SerialPortInfo[]>([])
  const serialStatus = ref<SerialStatus | null>(null)
  const blockMapping = ref<BlockMapping | null>(null)
  const snapshots = ref<Record<number, MotionSnapshot>>({})
  const realtimeData = ref<Record<number, RealtimeMotionData>>({})
  const linkDiagnostics = ref<Record<number, LinkDiagnostics>>({})
  const movementRecords = ref<Record<number, MovementRecord[]>>({})
  const movementDetails = ref<Record<string, MovementDetail>>({})
  const controlResults = ref<Record<number, DeviceCommandResult>>({})
  const loading = ref(false)
  const errorMessage = ref<string | null>(null)

  const testedCount = computed(() => devices.value.filter((device) => device.integrationStatus === 'tested').length)
  const pendingCount = computed(() => devices.value.filter((device) => device.integrationStatus === 'pending-integration').length)
  const onlineCount = computed(() => devices.value.filter((device) => device.integrationStatus === 'tested' && device.connectionStatus === 'online').length)
  const offlineCount = computed(() => devices.value.filter((device) => device.integrationStatus === 'tested' && device.connectionStatus === 'offline').length)
  const warningCount = computed(() => devices.value.filter((device) => device.integrationStatus === 'tested' && device.connectionStatus === 'warning').length)
  const allMovementRecords = computed(() => Object.values(movementRecords.value)
    .flat()
    .sort((left, right) => right.startedAt.localeCompare(left.startedAt)))

  function deviceById(deviceId: number): DeviceSnapshot | null {
    return devices.value.find((device) => device.deviceId === deviceId) ?? null
  }

  async function run<T>(action: () => Promise<T>): Promise<T> {
    loading.value = true
    errorMessage.value = null
    try {
      return await action()
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : String(error)
      throw error
    } finally {
      loading.value = false
    }
  }

  async function loadDevices(): Promise<void> {
    devices.value = await run(() => motionDataService.listDevices())
  }

  async function loadBlockMapping(): Promise<void> {
    blockMapping.value = await run(() => motionDataService.getBlockMapping())
  }

  async function updateBlockMapping(mapping: BlockMapping): Promise<void> {
    blockMapping.value = await run(() => motionDataService.updateBlockMapping(mapping))
    devices.value = await motionDataService.listDevices()
  }

  async function loadSerial(): Promise<void> {
    const [ports, status] = await run(() => Promise.all([
      motionDataService.listSerialPorts(),
      motionDataService.getSerialStatus(),
    ]))
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
    serialStatus.value = await run(() => motionDataService.openSerial({ port, baudRate }))
  }

  async function closeSerial(): Promise<void> {
    serialStatus.value = await run(() => motionDataService.closeSerial())
  }

  async function loadDeviceContext(deviceId: number): Promise<void> {
    const [snapshot, realtime, diagnostics, records] = await run(() => Promise.all([
      motionDataService.getMotionSnapshot({ deviceId }),
      motionDataService.getRealtimeData({ deviceId }),
      motionDataService.getLinkDiagnostics({ deviceId }),
      motionDataService.listMovements({ deviceId }),
    ]))
    snapshots.value[deviceId] = snapshot
    realtimeData.value[deviceId] = {
      ...realtime,
      trajectories: snapshot.trajectories,
    }
    linkDiagnostics.value[deviceId] = diagnostics
    movementRecords.value[deviceId] = records
  }

  async function refreshMotionSnapshot(deviceId: number): Promise<void> {
    const snapshot = await motionDataService.getMotionSnapshot({ deviceId })
    snapshots.value[deviceId] = snapshot
    const realtime = realtimeData.value[deviceId]
    if (
      realtime
      && realtime.trajectoryRevision === snapshot.trajectoryRevision
      && snapshot.trajectories.pcInertial.points.length > realtime.trajectories.pcInertial.points.length
    ) {
      realtimeData.value[deviceId] = { ...realtime, trajectories: snapshot.trajectories }
    }
  }

  async function refreshRealtimeData(deviceId: number): Promise<void> {
    const incoming = await motionDataService.getRealtimeData({ deviceId })
    const previous = realtimeData.value[deviceId]
    const reset = previous?.trajectoryRevision !== incoming.trajectoryRevision
    realtimeData.value[deviceId] = {
      ...incoming,
      trajectories: {
        device: mergeLiveTrajectory(previous?.trajectories.device, incoming.trajectories.device, reset),
        pcInertial: mergeLiveTrajectory(previous?.trajectories.pcInertial, incoming.trajectories.pcInertial, reset),
      },
    }
  }

  async function loadMovementRecords(deviceId: number, analysisMode?: AnalysisMode): Promise<void> {
    movementRecords.value[deviceId] = await run(() => motionDataService.listMovements({ deviceId, analysisMode }))
  }

  async function loadMovementRecordsForDevices(deviceIds: number[]): Promise<void> {
    const uniqueDeviceIds = [...new Set(deviceIds)]
    const entries = await run(() => Promise.all(uniqueDeviceIds.map(async (deviceId) => (
      [deviceId, await motionDataService.listMovements({ deviceId })] as const
    ))))
    for (const [deviceId, records] of entries) movementRecords.value[deviceId] = records
  }

  async function setAnalysisMode(deviceId: number, analysisMode: AnalysisMode): Promise<void> {
    snapshots.value[deviceId] = await run(() => motionDataService.setAnalysisMode({ deviceId, analysisMode }))
    realtimeData.value[deviceId] = await motionDataService.getRealtimeData({ deviceId })
    movementRecords.value[deviceId] = await motionDataService.listMovements({ deviceId, analysisMode })
  }

  async function resetAnalysis(deviceId: number): Promise<void> {
    snapshots.value[deviceId] = await run(() => motionDataService.resetAnalysis({ deviceId }))
    movementRecords.value[deviceId] = []
  }

  async function resetPacketCounters(deviceId: number): Promise<void> {
    const updated = await run(() => motionDataService.resetPacketCounters({ deviceId }))
    const index = devices.value.findIndex((device) => device.deviceId === deviceId)
    if (index >= 0) devices.value[index] = updated
    linkDiagnostics.value[deviceId] = await motionDataService.getLinkDiagnostics({ deviceId })
  }

  async function resetDataAndErrorCounters(deviceId: number): Promise<void> {
    const updated = await run(() => motionDataService.resetDataAndErrorCounters({ deviceId }))
    const index = devices.value.findIndex((device) => device.deviceId === deviceId)
    if (index >= 0) devices.value[index] = updated
  }

  async function executeCommand(deviceId: number, command: DeviceCommand): Promise<DeviceCommandResult> {
    const result = await run(() => motionDataService.executeDeviceCommand({ deviceId, command }))
    controlResults.value[deviceId] = result
    devices.value = await motionDataService.listDevices()
    return result
  }

  async function refreshBatteryVoltages(): Promise<BatteryRefreshResult> {
    const result = await run(() => motionDataService.refreshBatteryVoltages())
    devices.value = await motionDataService.listDevices()
    return result
  }

  async function loadMovementDetail(
    deviceId: number,
    movementId: number,
    analysisMode?: AnalysisMode,
  ): Promise<MovementDetail> {
    const detail = await run(() => motionDataService.getMovementDetail({ deviceId, movementId, analysisMode }))
    movementDetails.value[`${deviceId}:${analysisMode ?? 'pc'}:${movementId}`] = detail
    return detail
  }

  function movementDetailByKey(
    deviceId: number,
    movementId: number,
    analysisMode: AnalysisMode = 'pc',
  ): MovementDetail | null {
    return movementDetails.value[`${deviceId}:${analysisMode}:${movementId}`] ?? null
  }

  async function exportMovement(
    deviceId: number,
    movementId: number,
    format: MovementExportFormat,
    analysisMode?: AnalysisMode,
  ): Promise<MovementExport> {
    return run(() => motionDataService.exportMovement({ deviceId, movementId, analysisMode, format }))
  }

  return {
    devices,
    serialPorts,
    serialStatus,
    blockMapping,
    snapshots,
    realtimeData,
    linkDiagnostics,
    movementRecords,
    movementDetails,
    controlResults,
    loading,
    errorMessage,
    testedCount,
    pendingCount,
    onlineCount,
    offlineCount,
    warningCount,
    allMovementRecords,
    deviceById,
    loadDevices,
    loadBlockMapping,
    updateBlockMapping,
    loadSerial,
    refreshDeviceStatus,
    openSerial,
    closeSerial,
    loadDeviceContext,
    refreshMotionSnapshot,
    refreshRealtimeData,
    loadMovementRecords,
    loadMovementRecordsForDevices,
    setAnalysisMode,
    resetAnalysis,
    resetPacketCounters,
    resetDataAndErrorCounters,
    executeCommand,
    refreshBatteryVoltages,
    loadMovementDetail,
    movementDetailByKey,
    exportMovement,
  }
})

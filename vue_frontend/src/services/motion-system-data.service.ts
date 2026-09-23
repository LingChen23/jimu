import type { BlockMapping, DeviceHealth, DeviceSnapshot } from '@/types/device'
import type {
  AnalysisMode,
  AxisPoint,
  DeviceTrajectory,
  MotionSnapshot,
  MotionState,
  MovementDetail,
  MovementRecord,
  OrientationSnapshot,
  PcInertialTrajectory,
  RealtimeMotionData,
  SessionTremorAverage,
  SpeedStatistics,
  TremorMetrics,
} from '@/types/motion'
import type {
  AggregatorStatus,
  BatteryRefreshResult,
  DeviceCommandResult,
  LinkDiagnostics,
  SerialStatus,
} from '@/types/serial'
import type {
  CloudUploadSnapshot,
  CloudUploadState,
  ParticipantProfile,
  TaskResult,
  TaskSummary,
} from '@/types/task'
import { gatewayNameForDevice } from '@/config/system'
import type {
  DeviceCommandRequest,
  DeviceRequest,
  FinishTaskRequest,
  DeleteTaskRequest,
  MotionDataService,
  MovementDetailRequest,
  MovementExportRequest,
  OpenSerialRequest,
  RealtimeDataRequest,
  StartTaskRequest,
  TaskResultRequest,
  UpdateTaskParticipantRequest,
} from './motion-data.service'
import { fetchWithTimeout } from './http-client'
import { estimateAngularEventStart } from '@/utils/motion-event-interval'

type JsonObject = Record<string, any>

const emptyStatistics = () => ({
  movingSeconds: 0,
  stationarySeconds: 0,
  pauseCount: 0,
  pauseTotalSeconds: 0,
  strongShakeCount: 0,
  flipCount: 0,
  rotationCount: 0,
  spatialRotationDegrees: 0,
  spatialRotationTurns: 0,
  dropCount: 0,
  abnormalCount: 0,
})

function number(value: unknown, fallback = 0): number {
  const result = Number(value)
  return Number.isFinite(result) ? result : fallback
}

function nullableNumber(value: unknown): number | null {
  return value === null || value === undefined || !Number.isFinite(Number(value)) ? null : Number(value)
}

function motionState(value: unknown): MotionState {
  if (value === 'moving' || value === '移动中') return 'moving'
  if (value === 'stationary' || value === '静止') return 'stationary'
  if (value === 'calibrating' || value === '校准中') return 'calibrating'
  return 'unknown'
}

function analysisMode(value: unknown): AnalysisMode {
  return value === 'device' ? 'device' : 'pc'
}

function axisRows(rows: unknown): AxisPoint[] {
  return Array.isArray(rows) ? rows.flatMap((row) => (
    Array.isArray(row) && row.length >= 4
      ? [{ timestamp: number(row[0]) * 1_000, x: number(row[1]), y: number(row[2]), z: number(row[3]) }]
      : []
  )) : []
}

function trajectory(rows: unknown, source: 'device'): DeviceTrajectory
function trajectory(rows: unknown, source: PcInertialTrajectory['source']): PcInertialTrajectory
function trajectory(rows: unknown, source: PcInertialTrajectory['source'] | 'device'): DeviceTrajectory | PcInertialTrajectory {
  const points = Array.isArray(rows) ? rows.flatMap((row, index) => (
    Array.isArray(row) && row.length >= 3
      ? [{ timestamp: index, x: number(row[0]), y: number(row[1]), z: number(row[2]) }]
      : []
  )) : []
  return source === 'device'
    ? { source: 'device' as const, coordinateSystem: 'device-relative' as const, unit: 'm' as const, metricReliable: false as const, points }
    : { source: 'pc-inertial' as const, coordinateSystem: 'world-relative' as const, unit: 'm' as const, metricReliable: false as const, points }
}

function speed(raw: JsonObject | null | undefined): SpeedStatistics {
  return {
    minimumMps: nullableNumber(raw?.min),
    maximumMps: number(raw?.max),
    p05Mps: number(raw?.p05),
    averageMps: number(raw?.mean),
    medianMps: number(raw?.median),
    standardDeviationMps: number(raw?.std),
    p05Percentile: 5,
    sampleCount: number(raw?.sample_count),
  }
}

function tremor(raw: JsonObject | null | undefined): TremorMetrics {
  const band = Array.isArray(raw?.method?.band_hz) ? raw.method.band_hz : [0, 0]
  return {
    valid: Boolean(raw?.valid),
    dominantFrequencyHz: nullableNumber(raw?.dominant_frequency_hz),
    intensityDps: nullableNumber(raw?.intensity_dps),
    bandRmsDps: nullableNumber(raw?.band_rms_dps),
    frequencyBandHz: [number(band[0]), number(band[1])] as const,
    reason: raw?.reason ? String(raw.reason) : null,
  }
}

function sessionTremor(raw: JsonObject | null | undefined): SessionTremorAverage {
  return {
    validMovementCount: number(raw?.valid_movement_count),
    totalMovementCount: number(raw?.total_movement_count),
    meanFrequencyHz: nullableNumber(raw?.mean_frequency_hz),
    meanIntensityDps: nullableNumber(raw?.mean_intensity_dps),
    aggregation: String(raw?.aggregation ?? ''),
  }
}

function orientation(raw: JsonObject | null | undefined): OrientationSnapshot {
  const q = Array.isArray(raw?.quaternion_wxyz) ? raw.quaternion_wxyz : [1, 0, 0, 0]
  const euler = Array.isArray(raw?.euler_degrees) ? raw.euler_degrees : [0, 0, 0]
  const position = Array.isArray(raw?.position_m) ? raw.position_m : [0, 0, 0]
  const velocity = Array.isArray(raw?.velocity_mps) ? raw.velocity_mps : [0, 0, 0]
  return {
    initialized: Boolean(raw?.initialized),
    quaternion: { w: number(q[0], 1), x: number(q[1]), y: number(q[2]), z: number(q[3]) },
    rollDegrees: number(euler[0]),
    pitchDegrees: number(euler[1]),
    yawDegrees: number(euler[2]),
    yawReference: 'relative',
    positionM: { x: number(position[0]), y: number(position[1]), z: number(position[2]) },
    velocityMps: { x: number(velocity[0]), y: number(velocity[1]), z: number(velocity[2]) },
  }
}

function movement(raw: JsonObject, deviceId: number, mode: AnalysisMode): MovementRecord {
  const start = raw?.time?.start?.beijing ?? raw?.time?.start?.computer ?? new Date(0).toISOString()
  const end = raw?.time?.end?.beijing ?? raw?.time?.end?.computer ?? null
  return {
    movementId: number(raw.movement_id),
    deviceId,
    analysisMode: mode,
    state: raw.state === '移动中' ? 'active' : 'completed',
    startedAt: String(start),
    endedAt: end ? String(end) : null,
    durationSeconds: number(raw.segment_seconds ?? raw.moving_seconds),
    movingSeconds: number(raw.moving_seconds),
    sampleCount: number(raw.sample_count),
    statistics: {
      ...emptyStatistics(),
      movingSeconds: number(raw.moving_seconds),
      stationarySeconds: number(raw.stationary_seconds ?? raw.pause_total_seconds),
      pauseCount: number(raw.pause_count),
      pauseTotalSeconds: number(raw.pause_total_seconds),
      strongShakeCount: number(raw.shake_count),
      flipCount: number(raw.flip_count),
      rotationCount: number(raw.rotation_count),
      spatialRotationDegrees: number(raw.spatial_rotation_degrees),
      spatialRotationTurns: number(raw.spatial_rotation_turns),
      dropCount: number(raw.drop_count),
    },
    speed: speed(raw.speed),
    tremor: tremor(raw.tremor),
  }
}

function aggregator(raw: JsonObject | null | undefined): AggregatorStatus | null {
  if (!raw) return null
  return {
    usbReady: Boolean(raw.usb_ready),
    outputDropSeen: Boolean(raw.output_drop_seen),
    uptimeMs: number(raw.uptime_ms),
    usbRxBytes: number(raw.usb_rx_bytes),
    usbTxBytes: number(raw.usb_tx_bytes),
    usbValidCommands: number(raw.usb_valid_commands),
    usbInputErrors: number(raw.usb_input_errors),
    usbOutputDrops: number(raw.usb_output_drops),
    usbQueueHighWatermark: number(raw.usb_queue_high_watermark),
    ageSeconds: nullableNumber(raw.age_seconds),
    links: Array.isArray(raw.links) ? raw.links.map((link: JsonObject) => ({
      gatewayId: number(link.gateway_id),
      active: Boolean(link.active),
      rxQueuePending: Boolean(link.rx_queue_pending),
      commandQueuePending: Boolean(link.command_queue_pending),
      uartPort: number(link.uart_port),
      rxQueueHighWatermark: number(link.rx_queue_high_watermark),
      uartRxBytes: number(link.uart_rx_bytes),
      validFrames: number(link.valid_frames),
      cobsErrors: number(link.cobs_errors),
      crcErrors: number(link.crc_errors),
      frameErrors: number(link.frame_errors),
      frameDrops: number(link.frame_drops),
      uartOutputDrops: number(link.uart_output_drops),
      lastFrameAgeMs: nullableNumber(link.last_frame_age_ms),
    })) : [],
  }
}

function serialStatus(raw: JsonObject): SerialStatus {
  return {
    isOpen: Boolean(raw.open),
    port: raw.port ? String(raw.port) : null,
    baudRate: number(raw.baud, 921_600),
    receiveStatus: raw.error ? 'error' : raw.open ? 'receiving' : 'idle',
    errorMessage: raw.error ? String(raw.error) : null,
    byteCount: number(raw.byte_count),
    frameCount: number(raw.frame_count),
    encodedFrameCount: number(raw.encoded_frame_count),
    gatewayLostFrames: number(raw.gateway_lost_frames),
    cobsErrorCount: number(raw.cobs_error_count),
    crcErrorCount: number(raw.crc_error_count),
    frameErrorCount: number(raw.frame_error_count),
    overflowCount: number(raw.overflow_count),
    hostSessionReady: Boolean(raw.host_session_ready),
    hostSessionId: number(raw.host_session_id) > 0 ? number(raw.host_session_id) : null,
    aggregator: aggregator(raw.aggregator),
  }
}

export function taskSummary(raw: JsonObject): TaskSummary {
  const note = String(raw.note ?? '')
  return {
    taskId: String(raw.task_id),
    participantId: String(raw.participant_id),
    participantSnapshot: raw.participant_snapshot ? participantProfile(raw.participant_snapshot) : null,
    blockId: String(raw.block_id ?? ''),
    deviceId: number(raw.device_id),
    deviceIds: Array.isArray(raw.device_ids)
      ? raw.device_ids.map((deviceId: unknown) => number(deviceId))
      : [number(raw.device_id)],
    blockBindings: Array.isArray(raw.block_bindings)
      ? raw.block_bindings.map((binding: JsonObject) => ({
          deviceId: number(binding.device_id),
          blockId: String(binding.block_id ?? ''),
        }))
      : [{ deviceId: number(raw.device_id), blockId: String(raw.block_id ?? '') }],
    note,
    startTime: String(raw.start_time),
    endTime: raw.end_time ? String(raw.end_time) : null,
    durationSeconds: raw.duration_seconds === null ? null : number(raw.duration_seconds),
    totalScore: nullableNumber(raw.total_score),
    dataQuality: raw.data_quality === 'complete' || raw.data_quality === 'good' || raw.data_quality === 'poor' ? raw.data_quality : 'normal',
    status: raw.status === 'collecting' || raw.status === 'abnormal' || raw.status === 'cancelled' ? raw.status : 'completed',
    movementReferences: Array.isArray(raw.movement_references) ? raw.movement_references.map((reference: JsonObject) => ({
      deviceId: number(reference.device_id),
      blockId: reference.block_id ? String(reference.block_id) : undefined,
      movementId: number(reference.movement_id),
      analysisMode: analysisMode(reference.analysis_mode),
      startedAt: reference.started_at ? String(reference.started_at) : null,
      endedAt: reference.ended_at ? String(reference.ended_at) : null,
      durationSeconds: nullableNumber(reference.duration_seconds),
      taskMovementIndex: reference.task_movement_index ? number(reference.task_movement_index) : undefined,
      deviceMovementIndex: reference.device_movement_index ? number(reference.device_movement_index) : undefined,
      score: nullableNumber(reference.score),
      scoreModelVersion: reference.score_model_version ? String(reference.score_model_version) : null,
    })) : [],
  }
}

function participantProfile(raw: JsonObject): ParticipantProfile {
  return {
    participantId: String(raw.participant_id ?? ''),
    name: String(raw.name ?? ''),
    gender: String(raw.gender ?? ''),
    age: nullableNumber(raw.age),
    birthDate: String(raw.birth_date ?? ''),
    educationLevel: String(raw.education_level ?? ''),
    dominantHand: String(raw.dominant_hand ?? ''),
    phone: String(raw.phone ?? ''),
    visionStatus: String(raw.vision_status ?? ''),
    hearingStatus: String(raw.hearing_status ?? ''),
    upperLimbStatus: String(raw.upper_limb_status ?? ''),
    cognitiveStatus: String(raw.cognitive_status ?? ''),
    profileNote: String(raw.profile_note ?? ''),
  }
}

function participantProfilePayload(profile: ParticipantProfile): JsonObject {
  return {
    participant_id: profile.participantId,
    name: profile.name,
    gender: profile.gender,
    age: profile.age,
    birth_date: profile.birthDate,
    education_level: profile.educationLevel,
    dominant_hand: profile.dominantHand,
    phone: profile.phone,
    vision_status: profile.visionStatus,
    hearing_status: profile.hearingStatus,
    upper_limb_status: profile.upperLimbStatus,
    cognitive_status: profile.cognitiveStatus,
    profile_note: profile.profileNote,
  }
}

function analyzedTrajectory(rows: unknown): PcInertialTrajectory {
  const points = Array.isArray(rows) ? rows.flatMap((row) => (
    Array.isArray(row) && row.length >= 5
      ? [{
          timestamp: number(row[0]) * 1_000,
          x: number(row[1]),
          y: number(row[2]),
          z: number(row[3]),
          speedMps: number(row[4]),
        }]
      : []
  )) : []
  return {
    source: 'pc-inertial',
    coordinateSystem: 'world-relative',
    unit: 'm',
    metricReliable: false,
    points,
  }
}

function trajectoryWithSpeed(pathRows: unknown, speedRows: unknown): PcInertialTrajectory {
  if (!Array.isArray(pathRows) || !Array.isArray(speedRows)) return trajectory(pathRows, 'pc-inertial')
  const points = pathRows.flatMap((row, index) => {
    if (!Array.isArray(row) || row.length < 3) return []
    const speedRow = speedRows[Math.max(0, index - 1)]
    const elapsed = Array.isArray(speedRow) ? number(speedRow[0]) : index
    const speedMps = Array.isArray(speedRow) ? number(speedRow[1]) : 0
    return [{ timestamp: elapsed * 1_000, x: number(row[0]), y: number(row[1]), z: number(row[2]), speedMps }]
  })
  return { source: 'pc-inertial', coordinateSystem: 'world-relative', unit: 'm', metricReliable: false, points }
}

function linkDiagnostics(deviceId: number, raw: JsonObject | null | undefined): LinkDiagnostics {
  return {
    deviceId,
    connected: Boolean(raw?.connected),
    ready: Boolean(raw?.ready),
    connectionIntervalMs: nullableNumber(raw?.connection_interval_ms),
    attMtu: nullableNumber(raw?.att_mtu),
    dleValid: Boolean(raw?.dle_valid),
    dleRequestResult: String(raw?.dle_request_result_text ?? '未请求'),
    maxTxOctets: nullableNumber(raw?.max_tx_octets),
    maxTxTimeUs: nullableNumber(raw?.max_tx_time_us),
    maxRxOctets: nullableNumber(raw?.max_rx_octets),
    maxRxTimeUs: nullableNumber(raw?.max_rx_time_us),
    phyValid: Boolean(raw?.phy_valid),
    txPhy: raw?.tx_phy_text ? String(raw.tx_phy_text) : null,
    rxPhy: raw?.rx_phy_text ? String(raw.rx_phy_text) : null,
    rssiDbm: nullableNumber(raw?.rssi_dbm),
    receiveRate1sPps: number(raw?.receive_rate_1s_pps),
    receiveRate10sPps: number(raw?.receive_rate_10s_pps),
    receivedFrames: number(raw?.received_frames),
    sequenceGapFrames: number(raw?.sequence_gap_frames),
    connectCount: number(raw?.connect_count),
    disconnectCount: number(raw?.disconnect_count),
    connectionUpdateCount: number(raw?.connection_update_count),
    invalidFrames: number(raw?.invalid_frames),
    lastDisconnectReason: String(raw?.last_disconnect_reason_text ?? ''),
    gatewayQueueDrops: number(raw?.gateway_queue_drops),
    deviceQueueDepth: null,
    deviceQueueDrops: 0,
    bleResourceWaitCount: 0,
    diagnosticAgeSeconds: nullableNumber(raw?.age_seconds),
    linkLayerRetransmissions: nullableNumber(raw?.link_layer_retransmissions),
    linkLayerRetransmissionsNote: String(raw?.link_layer_retransmissions_note ?? ''),
  }
}

function health(control: JsonObject | null | undefined): DeviceHealth {
  const status = control?.status ?? {}
  const info = control?.info ?? {}
  return {
    batteryVoltageMv: nullableNumber(status.battery_mV),
    batteryLow: status.battery_low ?? null,
    firmware: info.firmware ? {
      version: String(info.firmware),
      motionProtocolVersion: number(info.motion_protocol_version),
      accelerationRangeG: number(info.accel_range_g),
      gyroscopeRangeDps: number(info.gyro_range_dps),
      sampleRateHz: number(info.sample_rate_hz),
      coordinateRateHz: number(info.coordinate_rate_hz),
      capabilities: number(info.capabilities),
    } : null,
    sensorStatus: status.sensor_ok === true ? 'ok' : status.sensor_ok === false ? 'error' : 'unknown',
    calibrated: status.calibrated ?? null,
    streaming: status.streaming ?? null,
    uptimeSeconds: nullableNumber(status.uptime_seconds),
    resetReason: status.reset_reason_text ? String(status.reset_reason_text) : null,
    latestSampleSequence: nullableNumber(status.latest_sample_sequence),
    queueDepth: nullableNumber(status.queue_depth),
    errors: {
      sampleGapCount: number(status.sample_gap_count),
      sensorErrorCount: number(status.sensor_error_count),
      queueDropCount: number(status.queue_drop_count),
      bleResourceWaitCount: number(status.ble_resource_wait_count),
      abnormalCount: number(status.sample_gap_count) + number(status.sensor_error_count) + number(status.queue_drop_count),
    },
  }
}

export class MotionSystemDataService implements MotionDataService {
  private async json(path: string, options?: RequestInit, timeoutMs?: number): Promise<JsonObject> {
    const response = await fetchWithTimeout(path, {
      ...options,
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...(options?.headers ?? {}) },
    }, timeoutMs)
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(String(payload.error ?? `请求失败：${response.status}`))
    return payload
  }

  async listSerialPorts() {
    const payload = await this.json('/api/ports')
    return (payload.ports ?? []).map((port: JsonObject) => ({
      port: String(port.device),
      description: String(port.description ?? port.device),
      nativeUsb: Boolean(port.native_usb),
    }))
  }

  async getSerialStatus() { return serialStatus(await this.json('/api/status')) }

  async openSerial(request: OpenSerialRequest) {
    return serialStatus(await this.json('/api/serial/open', {
      method: 'POST', body: JSON.stringify({ port: request.port, baud: request.baudRate }),
    }))
  }

  async closeSerial() {
    return serialStatus(await this.json('/api/serial/close', { method: 'POST', body: '{}' }))
  }

  async resetTransportCounters() {
    return serialStatus(await this.json('/api/serial/reset-counters', {
      method: 'POST', body: '{}',
    }))
  }

  async listDevices(): Promise<DeviceSnapshot[]> {
    const payload = await this.json('/api/devices')
    const controls = payload.controls ?? {}
    return (payload.devices ?? []).map((raw: JsonObject) => {
      const deviceId = number(raw.device_id)
      const control = controls[String(deviceId)] ?? {}
      const online = Boolean(raw.online)
      return {
        blockId: String(raw.block_id),
        deviceId,
        displayName: `积木 ${raw.block_id}`,
        integrationStatus: 'tested',
        connectionStatus: online ? 'online' : 'offline',
        usageStatus: online ? 'idle' : 'unavailable',
        motionState: motionState(raw.motion_state),
        currentTaskId: null,
        sessionId: raw.session_id === null ? '—' : String(raw.session_id),
        gatewayName: gatewayNameForDevice(deviceId),
        lastDataAgoMs: raw.age_seconds === null ? null : number(raw.age_seconds) * 1_000,
        packetCount: number(raw.packet_count),
        lostPacketCount: number(raw.lost_packets),
        dataQuality: !online ? 'unknown' : number(raw.lost_packets) === 0 ? 'good' : 'poor',
        batteryPercent: null,
        health: health(control),
        acceleration: [],
        gyroscope: [],
        statistics: emptyStatistics(),
        events: [],
      } satisfies DeviceSnapshot
    })
  }

  async getBlockMapping(): Promise<BlockMapping> {
    const raw = await this.json('/api/block-mapping')
    return {
      mappingVersion: String(raw.mapping_version),
      editableSource: raw.editable_source ? String(raw.editable_source) : null,
      devices: (raw.devices ?? []).map((entry: JsonObject) => ({ deviceId: number(entry.device_id), blockId: String(entry.block_id) })),
    }
  }

  async updateBlockMapping(mapping: BlockMapping): Promise<BlockMapping> {
    const raw = await this.json('/api/block-mapping', {
      method: 'POST',
      body: JSON.stringify({
        mapping_version: mapping.mappingVersion,
        devices: mapping.devices.map((entry) => ({ device_id: entry.deviceId, block_id: entry.blockId })),
      }),
    })
    return {
      mappingVersion: String(raw.mapping_version),
      editableSource: raw.editable_source ? String(raw.editable_source) : null,
      devices: (raw.devices ?? []).map((entry: JsonObject) => ({ deviceId: number(entry.device_id), blockId: String(entry.block_id) })),
    }
  }

  async getMotionSnapshot(request: DeviceRequest): Promise<MotionSnapshot> {
    const raw = await this.json(`/api/snapshot?device=${request.deviceId}`)
    const mode = analysisMode(raw.analysis_mode)
    const current = raw.current_movement ? movement(raw.current_movement, request.deviceId, mode) : null
    const completed = (raw.completed_movements ?? []).map((item: JsonObject) => movement(item, request.deviceId, mode))
    const selected = current ?? completed[0]
    return {
      deviceId: request.deviceId,
      capturedAt: Date.now(),
      available: Boolean(raw.online),
      analysisMode: mode,
      analysisModeLabel: String(raw.analysis_mode_label ?? ''),
      motionState: motionState(raw.motion_state),
      deviceReferenceState: String(raw.device_reference_state ?? ''),
      deviceModeState: motionState(raw.device_mode_state),
      pcModeState: motionState(raw.pc_mode_state),
      statistics: selected?.statistics ?? emptyStatistics(),
      speed: selected?.speed ?? speed(null),
      tremor: selected?.tremor ?? tremor(null),
      sessionTremorAverage: sessionTremor(raw.session_tremor_average),
      orientation: orientation(raw.orientation),
      trajectoryRevision: number(raw.trajectory_revision),
      trajectories: {
        device: trajectory(raw.received_path, 'device'),
        pcInertial: trajectory(raw.calculated_path, 'pc-inertial'),
      },
      currentMovement: current,
      completedMovements: completed,
      events: [],
    }
  }

  async getRealtimeData(request: RealtimeDataRequest): Promise<RealtimeMotionData> {
    const query = new URLSearchParams({ device: String(request.deviceId) })
    if (request.trajectoryRevision !== undefined) query.set('trajectory_revision', String(request.trajectoryRevision))
    if (request.deviceTrajectoryCursor !== undefined) query.set('received_after', String(request.deviceTrajectoryCursor))
    if (request.pcTrajectoryCursor !== undefined) query.set('calculated_after', String(request.pcTrajectoryCursor))
    const raw = await this.json(`/api/live?${query}`)
    const capturedAt = Date.now()
    const accel = axisRows(raw.accel_sample ? [raw.accel_sample] : [])
    const gyro = axisRows(raw.gyro_sample ? [raw.gyro_sample] : [])
    const delta = raw.trajectory_delta ?? {}
    const receivedDelta = delta.received ?? {}
    const calculatedDelta = delta.calculated ?? {}
    const deviceRows = Array.isArray(receivedDelta.points)
      ? receivedDelta.points
      : raw.received_position_m ? [raw.received_position_m] : []
    const pcRows = Array.isArray(calculatedDelta.points)
      ? calculatedDelta.points
      : raw.calculated_position_m ? [raw.calculated_position_m] : []
    const deviceTrajectory = trajectory(deviceRows, 'device')
    const pcTrajectory = trajectory(pcRows, 'pc-inertial')
    deviceTrajectory.points.forEach((point) => { point.timestamp = capturedAt })
    pcTrajectory.points.forEach((point) => { point.timestamp = capturedAt })
    return {
      deviceId: request.deviceId,
      capturedAt,
      available: Boolean(raw.online),
      analysisMode: analysisMode(raw.analysis_mode),
      motionState: motionState(raw.motion_state),
      acceleration: accel,
      gyroscope: gyro,
      latestAcceleration: accel[0] ?? null,
      latestGyroscope: gyro[0] ?? null,
      orientation: orientation(raw.orientation),
      trajectoryRevision: number(raw.trajectory_revision),
      trajectoryReset: Boolean(delta.reset),
      deviceTrajectoryCursor: nullableNumber(receivedDelta.cursor) ?? undefined,
      pcTrajectoryCursor: nullableNumber(calculatedDelta.cursor) ?? undefined,
      deviceTrajectoryTruncated: Boolean(receivedDelta.truncated),
      pcTrajectoryTruncated: Boolean(calculatedDelta.truncated),
      movementId: raw.movement_id === null ? null : number(raw.movement_id),
      trajectories: {
        device: deviceTrajectory,
        pcInertial: pcTrajectory,
      },
    }
  }

  async getLinkDiagnostics(request: DeviceRequest): Promise<LinkDiagnostics> {
    const raw = await this.json(`/api/snapshot?device=${request.deviceId}`)
    const result = linkDiagnostics(request.deviceId, raw.control?.link)
    result.deviceQueueDepth = nullableNumber(raw.control?.status?.queue_depth)
    result.deviceQueueDrops = number(raw.control?.status?.queue_drop_count)
    result.bleResourceWaitCount = number(raw.control?.status?.ble_resource_wait_count)
    return result
  }

  async resetDataAndErrorCounters(request: DeviceRequest): Promise<DeviceSnapshot> {
    await this.json('/api/device/reset-data-error-counters', {
      method: 'POST', body: JSON.stringify({ device: request.deviceId }),
    })
    const devices = await this.listDevices()
    const device = devices.find((item) => item.deviceId === request.deviceId)
    if (!device) throw new Error(`未知设备ID: ${request.deviceId}`)
    return device
  }

  async executeDeviceCommand(request: DeviceCommandRequest): Promise<DeviceCommandResult> {
    const raw = await this.json('/api/device/command', { method: 'POST', body: JSON.stringify({ device: request.deviceId, command: request.command }) })
    return {
      ok: Boolean(raw.ok),
      deviceId: number(raw.device_id, request.deviceId),
      command: request.command,
      requestId: number(raw.request_id),
      resultCode: number(raw.result),
      failureCode: null,
      message: String(raw.message ?? ''),
      executedAt: new Date().toISOString(),
      data: null,
    }
  }

  async refreshBatteryVoltages(): Promise<BatteryRefreshResult> {
    const raw = await this.json('/api/devices/battery-refresh', {
      method: 'POST', body: '{}',
    }, 25_000)
    return {
      requested: number(raw.requested),
      succeeded: number(raw.succeeded),
      skipped: number(raw.skipped),
      results: Array.isArray(raw.results) ? raw.results.map((item: JsonObject) => ({
        deviceId: number(item.device_id),
        ok: Boolean(item.ok),
        batteryVoltageMv: nullableNumber(item.battery_mV),
        batteryLow: item.battery_low === true ? true : item.battery_low === false ? false : null,
        message: String(item.message ?? ''),
      })) : [],
    }
  }

  async getMovementDetail(request: MovementDetailRequest): Promise<MovementDetail> {
    const mode = request.analysisMode ?? 'pc'
    const endpoint = request.taskId ? '/api/tasks/movement-detail' : '/api/movement/detail'
    const taskQuery = request.taskId ? `&task=${encodeURIComponent(request.taskId)}` : ''
    const raw = await this.json(`${endpoint}?device=${request.deviceId}&movement=${request.movementId}&mode=${mode}${taskQuery}`)
    const base = movement(raw, request.deviceId, mode)
    const movementStartedAt = Date.parse(base.startedAt)
    const gyroscopeSeries = axisRows(raw.gyroscope_series)
    const relativeEventTimestamp = (event: JsonObject, elapsedKey = 'elapsed_s', timeKey = 'time'): number => {
      const elapsed = nullableNumber(event[elapsedKey])
      if (elapsed !== null) return elapsed * 1_000
      const absolute = event[timeKey]?.computer ? Date.parse(event[timeKey].computer) : Number.NaN
      return Number.isFinite(absolute) && Number.isFinite(movementStartedAt)
        ? Math.max(0, absolute - movementStartedAt)
        : 0
    }
    const pauses = (raw.pause_events ?? []).map((event: JsonObject) => {
      const startTimestamp = relativeEventTimestamp({ ...event, time: event.start })
      const durationSeconds = number(event.duration_seconds)
      return {
        timestamp: startTimestamp,
        startTimestamp,
        endTimestamp: startTimestamp + durationSeconds * 1_000,
        durationSeconds,
        description: `停顿 ${durationSeconds.toFixed(2)} 秒`,
      }
    })
    const drops = (raw.drop_events ?? []).map((event: JsonObject) => {
      const endTimestamp = relativeEventTimestamp(event, 'impact_s', 'impact_time')
      const freefallStart = nullableNumber(event.freefall_start_s)
      const startTimestamp = freefallStart === null ? endTimestamp : freefallStart * 1_000
      const durationSeconds = Math.max(0, endTimestamp - startTimestamp) / 1_000
      return {
        timestamp: endTimestamp,
        startTimestamp,
        endTimestamp,
        durationSeconds,
        description: `掉落 ${durationSeconds.toFixed(2)} 秒，冲击 ${number(event.impact_g).toFixed(2)} g`,
      }
    })
    const events = [
      ...pauses.map((event: { timestamp: number; durationSeconds: number; description: string }, index: number) => ({ id: `pause-${index}`, type: 'pause' as const, title: '停顿', level: 'warning' as const, ...event })),
      ...drops.map((event: { timestamp: number; durationSeconds: number; description: string }, index: number) => ({ id: `drop-${index}`, type: 'drop' as const, title: '掉落', level: 'danger' as const, ...event })),
      ...(raw.shake_events ?? []).map((event: JsonObject, index: number) => ({
        id: `shake-${index}`, type: 'strong-shake' as const, title: '剧烈晃动', level: 'warning' as const,
        timestamp: relativeEventTimestamp(event),
        description: `剧烈晃动（${number(event.dynamic_acceleration_mps2).toFixed(2)} m/s²）`,
      })),
      ...(raw.flip_events ?? []).map((event: JsonObject, index: number) => {
        const endTimestamp = relativeEventTimestamp(event)
        const explicitStart = nullableNumber(event.start_elapsed_s)
        const startTimestamp = explicitStart === null
          ? estimateAngularEventStart(gyroscopeSeries, endTimestamp, 150, 600)
          : explicitStart * 1_000
        const durationSeconds = Math.max(0, endTimestamp - startTimestamp) / 1_000
        return {
          id: `flip-${index}`, type: 'flip' as const, title: '翻转', level: 'info' as const,
          timestamp: endTimestamp, startTimestamp, endTimestamp, durationSeconds,
          description: `第 ${index + 1} 次翻转，持续 ${durationSeconds.toFixed(2)} 秒`,
        }
      }),
      ...(raw.rotation_events ?? []).map((event: JsonObject, index: number) => {
        const endTimestamp = relativeEventTimestamp(event)
        const explicitStart = nullableNumber(event.start_elapsed_s)
        const degrees = nullableNumber(event.degrees) ?? 330
        const startTimestamp = explicitStart === null
          ? estimateAngularEventStart(gyroscopeSeries, endTimestamp, degrees, 1_000)
          : explicitStart * 1_000
        const durationSeconds = Math.max(0, endTimestamp - startTimestamp) / 1_000
        return {
          id: `rotation-${index}`, type: 'rotation' as const, title: '完整旋转', level: 'info' as const,
          timestamp: endTimestamp, startTimestamp, endTimestamp, durationSeconds,
          description: `第 ${index + 1} 次完整一轴旋转，持续 ${durationSeconds.toFixed(2)} 秒`,
        }
      }),
    ]
    return {
      ...base,
      acceleration: axisRows(raw.acceleration_series),
      gyroscope: gyroscopeSeries,
      dynamicAcceleration: axisRows(raw.dynamic_acceleration_series),
      speedSeries: (raw.speed_series ?? []).map((row: unknown[]) => ({ timestamp: number(row[0]) * 1_000, value: number(row[1]) })),
      orientation: orientation(null),
      events,
      pauseEvents: pauses,
      dropEvents: drops,
      trajectories: {
        device: trajectory(raw.received_path, 'device'),
        pcInertial: Array.isArray(raw.trajectory_series)
          ? analyzedTrajectory(raw.trajectory_series)
          : trajectoryWithSpeed(raw.calculated_path, raw.speed_series),
      },
      sessionTremorAverage: sessionTremor(raw.session_tremor_average),
    }
  }

  async exportMovement(request: MovementExportRequest) {
    const mode = request.analysisMode ?? 'pc'
    const endpoint = request.taskId
      ? `/api/tasks/movement-export.${request.format}`
      : `/api/movement/export.${request.format}`
    const taskQuery = request.taskId ? `&task=${encodeURIComponent(request.taskId)}` : ''
    const response = await fetchWithTimeout(
      `${endpoint}?device=${request.deviceId}&movement=${request.movementId}&mode=${mode}${taskQuery}`,
      {},
      15_000,
    )
    if (!response.ok) throw new Error(`导出失败：${response.status}`)
    return {
      fileName: `device_${request.deviceId}_movement_${request.movementId}_${mode}.${request.format}`,
      format: request.format,
      mimeType: request.format === 'csv' ? 'text/csv;charset=utf-8' as const : 'application/json;charset=utf-8' as const,
      content: await response.text(),
    }
  }

  async getCurrentTask(request: DeviceRequest): Promise<TaskSummary | null> {
    const raw = await this.json(`/api/tasks/current?device=${request.deviceId}`)
    return raw.task ? taskSummary(raw.task) : null
  }

  async listParticipants(): Promise<ParticipantProfile[]> {
    const raw = await this.json('/api/participants')
    return (raw.participants ?? []).map((item: JsonObject) => participantProfile(item))
  }

  async exportTask(request: TaskResultRequest) {
    const response = await fetchWithTimeout(
      `/api/tasks/export.zip?task=${encodeURIComponent(request.taskId)}`,
      {},
      120_000,
    )
    if (!response.ok) throw new Error(`整次任务导出失败：${response.status}`)
    return {
      fileName: `task_${request.taskId}.zip`,
      mimeType: 'application/zip' as const,
      content: await response.blob(),
    }
  }

  async exportTasks(request: { taskIds: string[] }) {
    const response = await fetchWithTimeout(
      '/api/tasks/export-batch.zip',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_ids: request.taskIds }),
      },
      120_000,
    )
    if (!response.ok) {
      let message = `批量导出失败：${response.status}`
      try {
        const payload = await response.json()
        if (payload?.error) message = String(payload.error)
      } catch {
        // Keep the status-based fallback for non-JSON server errors.
      }
      throw new Error(message)
    }
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    return {
      fileName: `tasks_batch_${stamp}.zip`,
      mimeType: 'application/zip' as const,
      content: await response.blob(),
    }
  }

  async startTask(request: StartTaskRequest): Promise<TaskSummary> {
    const raw = await this.json('/api/tasks/start', {
      method: 'POST',
      body: JSON.stringify({
        device_id: request.deviceId,
        participant_id: request.participantId,
        participant_profile: participantProfilePayload(request.participantProfile),
        note: request.note,
      }),
    })
    return taskSummary(raw.task)
  }

  async updateTaskParticipant(request: UpdateTaskParticipantRequest): Promise<TaskSummary> {
    const raw = await this.json('/api/tasks/participant', {
      method: 'POST',
      body: JSON.stringify({
        task_id: request.taskId,
        participant_profile: participantProfilePayload(request.participantProfile),
      }),
    })
    return taskSummary(raw.task)
  }

  async finishTask(request: FinishTaskRequest): Promise<TaskSummary> {
    const raw = await this.json('/api/tasks/finish', {
      method: 'POST',
      body: JSON.stringify({ task_id: request.taskId }),
    }, 30_000)
    return taskSummary(raw.task)
  }

  async listTasks(): Promise<TaskSummary[]> {
    const raw = await this.json('/api/tasks')
    return (raw.tasks ?? []).map((task: JsonObject) => ({ ...taskSummary(task), localAvailable: true }))
  }

  async listCloudTasks(page = 1, pageSize = 10): Promise<{
    tasks: TaskSummary[]
    total: number
    page: number
    pageSize: number
  }> {
    const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
    const raw = await this.json(`/api/cloud-history/tasks?${query}`)
    return {
      tasks: (raw.tasks ?? []).map((task: JsonObject) => ({
        ...taskSummary(task), localAvailable: false, cloudArchived: true,
      })),
      total: number(raw.total),
      page: number(raw.page, page),
      pageSize: number(raw.page_size, pageSize),
    }
  }

  async getCloudUploadStatus(): Promise<CloudUploadSnapshot> {
    const raw = await this.json('/api/cloud-upload/status')
    const tasks = Object.fromEntries(Object.entries(raw.tasks ?? {}).map(([taskId, value]) => {
      const item = value as JsonObject
      const status: CloudUploadState = item.status === 'uploading'
        || item.status === 'failed'
        || item.status === 'uploaded'
        ? item.status
        : 'pending'
      return [taskId, {
        status,
        errorCode: item.error_code ? String(item.error_code) : null,
        retryAt: nullableNumber(item.retry_at),
      }]
    }))
    const connection = raw.connection === 'connected' || raw.connection === 'disconnected'
      ? raw.connection
      : 'disabled'
    return { connection, tasks }
  }

  async enqueueCloudUpload(taskIds: string[]): Promise<void> {
    const raw = await this.json('/api/cloud-upload/enqueue', {
      method: 'POST',
      body: JSON.stringify({ task_ids: taskIds }),
    })
    const failed = Object.values(raw.tasks ?? {}).find((item: any) => !item?.ok) as JsonObject | undefined
    if (failed) throw new Error(String(failed.error_code ?? '加入上传队列失败'))
  }

  async getTaskResult(request: TaskResultRequest): Promise<TaskResult> {
    const raw = await this.json(`/api/tasks/result?task=${encodeURIComponent(request.taskId)}`)
    const task = taskSummary(raw.task)
    const reference = raw.movement_reference ? {
      deviceId: number(raw.movement_reference.device_id),
      blockId: raw.movement_reference.block_id ? String(raw.movement_reference.block_id) : undefined,
      movementId: number(raw.movement_reference.movement_id),
      analysisMode: analysisMode(raw.movement_reference.analysis_mode),
      startedAt: raw.movement_reference.started_at ? String(raw.movement_reference.started_at) : null,
      endedAt: raw.movement_reference.ended_at ? String(raw.movement_reference.ended_at) : null,
      durationSeconds: nullableNumber(raw.movement_reference.duration_seconds),
      taskMovementIndex: raw.movement_reference.task_movement_index ? number(raw.movement_reference.task_movement_index) : undefined,
      deviceMovementIndex: raw.movement_reference.device_movement_index ? number(raw.movement_reference.device_movement_index) : undefined,
      score: nullableNumber(raw.movement_reference.score),
      scoreModelVersion: raw.movement_reference.score_model_version ? String(raw.movement_reference.score_model_version) : null,
    } : null
    const rawScore = raw.score ?? {}
    return {
      task,
      movementReference: reference,
      score: {
        totalScore: nullableNumber(rawScore.total_score ?? task.totalScore),
        scoreStatus: rawScore.score_status === 'completed' || rawScore.score_status === 'processing' || rawScore.score_status === 'failed'
          ? rawScore.score_status
          : 'pending',
        modelVersion: rawScore.model_version ? String(rawScore.model_version) : null,
        explanation: Array.isArray(rawScore.explanation) ? rawScore.explanation.map(String) : [],
        demoOnly: Boolean(rawScore.demo_only),
      },
      statistics: emptyStatistics(),
      acceleration: [],
      gyroscope: [],
      events: [],
    }
  }

  async deleteTask(request: DeleteTaskRequest): Promise<TaskSummary> {
    const raw = await this.json('/api/tasks/delete', {
      method: 'POST',
      body: JSON.stringify({ task_id: request.taskId }),
    })
    return taskSummary(raw.task)
  }

  async getTaskMovementDetail(
    request: MovementDetailRequest & { taskId: string },
  ): Promise<MovementDetail> {
    return this.getMovementDetail(request)
  }
}

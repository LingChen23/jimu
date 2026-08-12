import type {
  AnalysisMode,
  MotionEvent,
  MotionStatistics,
  MovementRecord,
  OrientationSnapshot,
  SessionTremorAverage,
  SpeedStatistics,
  TremorMetrics,
} from '@/types/motion'
import { MOCK_BASE_TIME_MS, roundMockValue } from './motion-series'

export function makeMotionStatistics(deviceId: number, available = true): MotionStatistics {
  if (!available) {
    return {
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
      suspectedDropCount: 0,
      abnormalCount: 0,
    }
  }
  const dropCount = deviceId === 4 ? 1 : 0
  return {
    movingSeconds: roundMockValue(41.5 + deviceId * 2.3, 1),
    stationarySeconds: roundMockValue(18.2 + deviceId * 1.4, 1),
    pauseCount: 1 + (deviceId % 2),
    pauseTotalSeconds: roundMockValue(1.1 + deviceId * 0.3, 1),
    strongShakeCount: deviceId % 3,
    flipCount: 1 + (deviceId % 2),
    rotationCount: deviceId % 4,
    spatialRotationDegrees: (deviceId % 4) * 360,
    spatialRotationTurns: deviceId % 4,
    dropCount,
    suspectedDropCount: dropCount,
    abnormalCount: deviceId === 4 ? 2 : deviceId % 2,
  }
}

export function makeSpeedStatistics(deviceId: number, available = true): SpeedStatistics {
  if (!available) {
    return {
      minimumMps: 0,
      maximumMps: 0,
      p05Mps: 0,
      averageMps: 0,
      medianMps: 0,
      standardDeviationMps: 0,
      p05Percentile: 5,
      sampleCount: 0,
    }
  }
  return {
    minimumMps: roundMockValue(0.035 + deviceId * 0.006, 3),
    maximumMps: roundMockValue(0.62 + deviceId * 0.08, 3),
    p05Mps: roundMockValue(0.08 + deviceId * 0.01, 3),
    averageMps: roundMockValue(0.29 + deviceId * 0.025, 3),
    medianMps: roundMockValue(0.27 + deviceId * 0.023, 3),
    standardDeviationMps: roundMockValue(0.11 + deviceId * 0.008, 3),
    p05Percentile: 5,
    sampleCount: 92 + deviceId * 4,
  }
}

export function makeTremorMetrics(deviceId: number, available = true): TremorMetrics {
  return available
    ? {
        valid: true,
        dominantFrequencyHz: roundMockValue(5.2 + deviceId * 0.35, 2),
        intensityDps: roundMockValue(7.4 + deviceId * 0.9, 2),
        bandRmsDps: roundMockValue(4.1 + deviceId * 0.55, 2),
        frequencyBandHz: [3, 20],
        reason: null,
      }
    : {
        valid: false,
        dominantFrequencyHz: null,
        intensityDps: null,
        bandRmsDps: null,
        frequencyBandHz: [3, 20],
        reason: '设备待接入',
      }
}

export function makeSessionTremorAverage(deviceId: number, available = true): SessionTremorAverage {
  return {
    validMovementCount: available ? 2 : 0,
    totalMovementCount: available ? 2 : 0,
    meanFrequencyHz: available ? roundMockValue(5 + deviceId * 0.3, 2) : null,
    meanIntensityDps: available ? roundMockValue(7 + deviceId * 0.75, 2) : null,
    aggregation: '已完成移动的有效FFT主频和强度算术平均',
  }
}

export function makeOrientation(deviceId: number, available = true): OrientationSnapshot {
  if (!available) {
    return {
      initialized: false,
      quaternion: { w: 1, x: 0, y: 0, z: 0 },
      rollDegrees: 0,
      pitchDegrees: 0,
      yawDegrees: 0,
      yawReference: 'relative',
      positionM: { x: 0, y: 0, z: 0 },
      velocityMps: { x: 0, y: 0, z: 0 },
    }
  }
  return {
    initialized: true,
    quaternion: {
      w: roundMockValue(0.98 - deviceId * 0.004),
      x: roundMockValue(0.05 + deviceId * 0.006),
      y: roundMockValue(0.08 + deviceId * 0.004),
      z: roundMockValue(0.12 + deviceId * 0.005),
    },
    rollDegrees: roundMockValue(5.5 + deviceId * 1.2, 1),
    pitchDegrees: roundMockValue(-3.2 + deviceId * 0.8, 1),
    yawDegrees: roundMockValue(18 + deviceId * 6.5, 1),
    yawReference: 'relative',
    positionM: {
      x: roundMockValue(deviceId * 0.04),
      y: roundMockValue(deviceId * 0.025),
      z: roundMockValue(deviceId * 0.008),
    },
    velocityMps: {
      x: roundMockValue(0.12 + deviceId * 0.01),
      y: roundMockValue(0.05 + deviceId * 0.006),
      z: 0,
    },
  }
}

export function makeTrajectories(deviceId: number, available = true) {
  const count = available ? 36 : 0
  const start = MOCK_BASE_TIME_MS + deviceId * 60_000
  const deviceOriginY = Math.sin(deviceId / 6) * 0.45
  const deviceOriginZ = Math.cos(deviceId / 9) * 0.22
  const pcOriginY = Math.sin(deviceId / 6) * 0.06
  const pcOriginZ = Math.cos(deviceId / 9) * 0.018
  return {
    device: {
      source: 'device' as const,
      coordinateSystem: 'device-relative' as const,
      unit: 'm' as const,
      metricReliable: false as const,
      points: Array.from({ length: count }, (_, index) => ({
        timestamp: start + index * 100,
        x: roundMockValue(index / 35),
        y: roundMockValue(Math.sin((index + deviceId) / 6) * 0.45 - deviceOriginY),
        z: roundMockValue(Math.cos((index + deviceId) / 9) * 0.22 - deviceOriginZ),
      })),
    },
    pcInertial: {
      source: 'pc-inertial' as const,
      coordinateSystem: 'world-relative' as const,
      unit: 'm' as const,
      metricReliable: false as const,
      points: Array.from({ length: count }, (_, index) => ({
        timestamp: start + index * 100,
        x: roundMockValue(index * (0.008 + deviceId * 0.0004)),
        y: roundMockValue(Math.sin((index + deviceId) / 6) * 0.06 - pcOriginY),
        z: roundMockValue(Math.cos((index + deviceId) / 9) * 0.018 - pcOriginZ),
      })),
    },
  }
}

export function makeMotionEvents(deviceId: number, available = true): MotionEvent[] {
  if (!available) return []
  const definitions: Array<Pick<MotionEvent, 'type' | 'title' | 'description' | 'level'>> = [
    { type: 'moving', title: '开始移动', description: '检测到设备进入移动状态', level: 'info' },
    { type: 'stationary', title: '静止', description: '设备恢复稳定静止状态', level: 'info' },
    { type: 'pause', title: '停顿', description: '移动过程中出现短暂停顿', level: 'info' },
    { type: 'rotation', title: '旋转', description: '检测到连续角速度变化', level: 'info' },
    { type: 'flip', title: '翻转', description: '检测到设备姿态明显翻转', level: 'warning' },
    { type: 'strong-shake', title: '剧烈晃动', description: '短时间内加速度变化超过阈值', level: 'warning' },
    { type: 'drop', title: '疑似掉落', description: '检测到低重力后冲击特征', level: deviceId === 4 ? 'danger' : 'warning' },
  ]
  return definitions.map((definition, index) => ({
    id: `${deviceId}-event-${index + 1}`,
    deviceId,
    timestamp: MOCK_BASE_TIME_MS + deviceId * 60_000 + index * 1_800,
    ...definition,
  }))
}

export function makeMovementRecord(
  deviceId: number,
  movementId: number,
  analysisMode: AnalysisMode,
  state: 'active' | 'completed' = 'completed',
): MovementRecord {
  const startMs = MOCK_BASE_TIME_MS - movementId * 180_000 + deviceId * 10_000
  const durationSeconds = roundMockValue(5.4 + movementId * 1.3 + deviceId * 0.4, 1)
  return {
    movementId,
    deviceId,
    analysisMode,
    state,
    startedAt: new Date(startMs).toISOString(),
    endedAt: state === 'completed' ? new Date(startMs + durationSeconds * 1_000).toISOString() : null,
    durationSeconds,
    movingSeconds: roundMockValue(durationSeconds - 0.9, 1),
    sampleCount: 72 + movementId * 8 + deviceId * 3,
    statistics: makeMotionStatistics(deviceId),
    speed: makeSpeedStatistics(deviceId),
    tremor: makeTremorMetrics(deviceId),
  }
}

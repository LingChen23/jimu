import type { DeviceSnapshot } from '@/types/device'

export type BadgeTone = 'green' | 'blue' | 'orange' | 'red' | 'gray' | 'purple'

export function integrationText(device: DeviceSnapshot): string {
  return device.integrationStatus === 'tested' ? '已实测' : '待接入'
}

export function connectionText(device: DeviceSnapshot): string {
  if (device.integrationStatus !== 'tested') return '未接入'
  return device.connectionStatus === 'offline' ? '离线' : '在线'
}

export function connectionTone(device: DeviceSnapshot): BadgeTone {
  return device.integrationStatus === 'tested' && device.connectionStatus !== 'offline'
    ? 'green'
    : 'gray'
}

export function usageText(device: DeviceSnapshot): string {
  if (device.integrationStatus === 'pending-integration') return '不可用'
  if (device.usageStatus === 'idle') return '空闲'
  if (device.usageStatus === 'collecting') return '采集中'
  return '不可用'
}

export function usageTone(device: DeviceSnapshot): BadgeTone {
  if (device.usageStatus === 'unavailable') return 'gray'
  return device.usageStatus === 'collecting' ? 'purple' : 'blue'
}

export function currentTaskText(device: DeviceSnapshot): string {
  if (device.integrationStatus === 'pending-integration') return '—'
  return device.currentTaskId ?? '暂无任务'
}

export function batteryVoltageText(device: DeviceSnapshot): string {
  const value = device.health.batteryVoltageMv
  return device.integrationStatus === 'tested' && value !== null ? `${value} mV` : '—'
}

export function batteryLevelText(device: DeviceSnapshot): string {
  const value = device.health.batteryVoltageMv
  if (device.integrationStatus !== 'tested' || value === null) return '—'
  if (value >= 3800) return '高'
  return value >= 3401 ? '中' : '低'
}

export function batteryLevelTone(device: DeviceSnapshot): BadgeTone {
  const level = batteryLevelText(device)
  if (level === '高') return 'green'
  if (level === '中') return 'orange'
  return level === '低' ? 'red' : 'gray'
}

export function firmwareVersionText(device: DeviceSnapshot): string {
  return device.health.firmware ? `v${device.health.firmware.version}` : '—'
}

export function sensorText(device: DeviceSnapshot): string {
  if (device.integrationStatus !== 'tested') return '—'
  if (device.health.sensorStatus === 'ok') return '正常'
  if (device.health.sensorStatus === 'error') return '故障'
  return '未知'
}

export function calibrationText(device: DeviceSnapshot): string {
  const value = device.health.calibrated
  if (device.integrationStatus !== 'tested' || value === null) return '—'
  return value ? '已校准' : '未校准'
}

export function streamingText(device: DeviceSnapshot): string {
  const value = device.health.streaming
  if (device.integrationStatus !== 'tested' || value === null) return '—'
  return value ? '数据流开启' : '数据流停止'
}

export function uptimeText(device: DeviceSnapshot): string {
  const total = device.health.uptimeSeconds
  if (device.integrationStatus !== 'tested' || total === null) return '—'
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  return `${hours}小时 ${minutes}分 ${seconds}秒`
}

export function packetCountText(device: DeviceSnapshot): string {
  return device.integrationStatus === 'tested' ? device.packetCount.toLocaleString() : '—'
}

export function packetLossText(device: DeviceSnapshot): string {
  return device.integrationStatus === 'tested' ? device.lostPacketCount.toLocaleString() : '—'
}

export function qualityText(device: DeviceSnapshot): string {
  if (device.integrationStatus !== 'tested') return '—'
  return ({ good: '良好', normal: '一般', poor: '较差', unknown: '未知' } as const)[device.dataQuality]
}

export function qualityTone(device: DeviceSnapshot): BadgeTone {
  if (device.integrationStatus !== 'tested' || device.dataQuality === 'unknown') return 'gray'
  if (device.dataQuality === 'good') return 'green'
  return device.dataQuality === 'normal' ? 'orange' : 'red'
}

export function motionText(device: DeviceSnapshot): string {
  if (device.integrationStatus !== 'tested') return '—'
  return ({ stationary: '静止', moving: '移动中', calibrating: '校准中', unknown: '未知' } as const)[device.motionState]
}

export function lastDataText(device: DeviceSnapshot): string {
  if (device.integrationStatus !== 'tested') return '—'
  return device.lastDataAgoMs === null ? '无数据' : `${device.lastDataAgoMs} ms 前`
}

export function nullableCount(value: number | null): string {
  return value === null ? '—' : value.toLocaleString()
}

export const DEVICE_COUNT = 10
export const DEVICE_IDS = Array.from({ length: DEVICE_COUNT }, (_, index) => index + 1)
export const BLOCK_IDS = Array.from(
  { length: DEVICE_COUNT },
  (_, index) => String.fromCharCode(65 + index),
)

const BLOCK_NAMES: Record<string, string> = {
  A: '圆柱块', B: '四面尖块', C: '十字块', D: '扇锥块', E: '三翼块',
  F: '弧翼块', G: '螺纹筒', H: '弧槽块', I: '双桥块', J: '多半圆块',
}

export function blockName(blockId: string | null | undefined): string {
  return blockId ? BLOCK_NAMES[blockId] ?? blockId : '—'
}

export function gatewayNameForDevice(deviceId: number): string {
  if (deviceId >= 1 && deviceId <= 4) return '网关A'
  if (deviceId >= 5 && deviceId <= 7) return '网关B'
  if (deviceId >= 8 && deviceId <= 10) return '网关C'
  return '—'
}

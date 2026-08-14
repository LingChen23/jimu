export const DEVICE_COUNT = 10
export const DEVICE_IDS = Array.from({ length: DEVICE_COUNT }, (_, index) => index + 1)
export const BLOCK_IDS = Array.from(
  { length: DEVICE_COUNT },
  (_, index) => String.fromCharCode(65 + index),
)

export function gatewayNameForDevice(deviceId: number): string {
  if (deviceId >= 1 && deviceId <= 4) return '网关A'
  if (deviceId >= 5 && deviceId <= 7) return '网关B'
  if (deviceId >= 8 && deviceId <= 10) return '网关C'
  return '—'
}

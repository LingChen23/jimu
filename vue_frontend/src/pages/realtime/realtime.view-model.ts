import type { DeviceSnapshot } from '@/types/device'
import type { TaskSummary } from '@/types/task'

export function isTaskBoundToDevice(
  task: TaskSummary | null,
  device: DeviceSnapshot | null,
): boolean {
  if (!task || !device) return false
  const deviceIds = task.deviceIds?.length ? task.deviceIds : [task.deviceId]
  if (!deviceIds.includes(device.deviceId)) return false
  const binding = task.blockBindings?.find((entry) => entry.deviceId === device.deviceId)
  return !binding || binding.blockId === device.blockId
}

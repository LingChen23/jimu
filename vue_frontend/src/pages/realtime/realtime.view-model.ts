import type { DeviceSnapshot } from '@/types/device'
import type { TaskSummary } from '@/types/task'

export const MOCK_REALTIME_SCORE = 82

export interface ScoreView {
  title: '等待采集' | '特征采集中'
  detail: '开始任务后采集运动特征' | '正式评分模型待接入'
  demoScore: number | null
}

export function isTaskBoundToDevice(
  task: TaskSummary | null,
  device: DeviceSnapshot | null,
): boolean {
  return Boolean(task && device
    && task.deviceId === device.deviceId
    && task.blockId === device.blockId)
}

export function getScoreView(task: TaskSummary | null): ScoreView {
  return task
    ? { title: '特征采集中', detail: '正式评分模型待接入', demoScore: MOCK_REALTIME_SCORE }
    : { title: '等待采集', detail: '开始任务后采集运动特征', demoScore: null }
}

export function formatElapsedTime(totalSeconds: number | null | undefined): string {
  if (totalSeconds === null || totalSeconds === undefined) return '—'
  const safeSeconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(safeSeconds / 3_600)
  const minutes = Math.floor((safeSeconds % 3_600) / 60)
  const seconds = safeSeconds % 60
  const parts = [minutes, seconds].map((part) => String(part).padStart(2, '0'))
  return hours ? `${hours}:${parts.join(':')}` : parts.join(':')
}

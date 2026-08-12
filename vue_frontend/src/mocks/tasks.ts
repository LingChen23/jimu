import type { TaskResult, TaskSummary } from '@/types/task'
import {
  makeAccelerationSeries,
  makeGyroscopeSeries,
  makeMotionEvents,
  makeMotionStatistics,
} from './motion'

export const mockCurrentTask: TaskSummary = {
  taskId: 'T20260729-001',
  participantId: 'U001',
  blockId: 'BLOCK-03',
  deviceId: 3,
  deviceIds: [3],
  taskType: '积木搭建任务A',
  startTime: '2026-07-29 14:20:00',
  endTime: null,
  durationSeconds: 75,
  totalScore: null,
  dataQuality: 'good',
  status: 'collecting',
}

export const mockTasks: TaskSummary[] = [
  { taskId: 'T20260729-001', participantId: 'U001', blockId: 'BLOCK-03', deviceId: 3, deviceIds: [3], taskType: '积木搭建任务A', startTime: '2026-07-29 14:20:00', endTime: '2026-07-29 14:22:15', durationSeconds: 135, totalScore: 82, dataQuality: 'complete', status: 'completed' },
  { taskId: 'T20260729-002', participantId: 'U002', blockId: 'BLOCK-01', deviceId: 1, deviceIds: [1], taskType: '平衡控制任务B', startTime: '2026-07-29 13:48:00', endTime: '2026-07-29 13:50:01', durationSeconds: 121, totalScore: 74, dataQuality: 'good', status: 'completed' },
  { taskId: 'T20260728-018', participantId: 'U003', blockId: 'BLOCK-02', deviceId: 2, deviceIds: [2], taskType: '路径跟随任务C', startTime: '2026-07-28 16:05:00', endTime: '2026-07-28 16:07:34', durationSeconds: 154, totalScore: 91, dataQuality: 'complete', status: 'completed' },
  { taskId: 'T20260728-017', participantId: 'U004', blockId: 'BLOCK-04', deviceId: 4, deviceIds: [4], taskType: '积木搭建任务A', startTime: '2026-07-28 15:32:00', endTime: '2026-07-28 15:34:05', durationSeconds: 125, totalScore: 58, dataQuality: 'poor', status: 'completed' },
  { taskId: 'T20260727-016', participantId: 'U005', blockId: 'BLOCK-02', deviceId: 2, deviceIds: [2], taskType: '平衡控制任务B', startTime: '2026-07-27 14:10:00', endTime: '2026-07-27 14:12:17', durationSeconds: 137, totalScore: 79, dataQuality: 'good', status: 'completed' },
  { taskId: 'T20260727-009', participantId: 'U006', blockId: 'BLOCK-04', deviceId: 4, deviceIds: [4], taskType: '路径跟随任务C', startTime: '2026-07-27 16:58:00', endTime: '2026-07-27 17:00:42', durationSeconds: 162, totalScore: 88, dataQuality: 'complete', status: 'completed' },
]

export function getMockTaskResult(taskId: string): TaskResult {
  const task = mockTasks.find((item) => item.taskId === taskId)
  if (!task) throw new Error(`未找到任务记录: ${taskId}`)
  const statistics = makeMotionStatistics(task.deviceId)
  return {
    task,
    score: {
      totalScore: task.totalScore,
      scoreStatus: 'completed',
      modelVersion: '示例模型 V0',
      explanation: [
        '任务已完整完成',
        `检测到 ${statistics.strongShakeCount} 次剧烈晃动`,
        statistics.suspectedDropCount ? '检测到疑似掉落，请复核' : '未检测到疑似掉落',
      ],
      demoOnly: true,
    },
    statistics,
    acceleration: makeAccelerationSeries(task.deviceId),
    gyroscope: makeGyroscopeSeries(task.deviceId),
    events: makeMotionEvents(task.deviceId),
  }
}

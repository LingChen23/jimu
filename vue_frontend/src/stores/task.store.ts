import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { motionDataService } from '@/services/data.service'
import type { TaskResult, TaskSummary } from '@/types/task'

export const useTaskStore = defineStore('task', () => {
  const currentTask = ref<TaskSummary | null>(null)
  const tasks = ref<TaskSummary[]>([])
  const taskResults = ref<Record<string, TaskResult>>({})
  const loading = ref(false)
  const errorMessage = ref<string | null>(null)

  const completedCount = computed(() => tasks.value.filter((task) => task.status === 'completed').length)
  const abnormalCount = computed(() => tasks.value.filter((task) => task.status === 'abnormal').length)
  const averageScore = computed(() => {
    const scores = tasks.value
      .map((task) => task.totalScore)
      .filter((score): score is number => score !== null)
    if (!scores.length) return null
    return scores.reduce((sum, score) => sum + score, 0) / scores.length
  })

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

  async function loadCurrentTask(deviceId: number): Promise<void> {
    currentTask.value = await run(() => motionDataService.getCurrentTask({ deviceId }))
  }

  async function startTask(deviceId: number, participantId: string, note: string): Promise<TaskSummary> {
    const task = await run(() => motionDataService.startTask({ deviceId, participantId, note }))
    currentTask.value = task
    tasks.value = [task, ...tasks.value.filter((item) => item.taskId !== task.taskId)]
    return task
  }

  async function finishTask(taskId: string): Promise<TaskSummary> {
    const task = await run(() => motionDataService.finishTask({ taskId }))
    currentTask.value = null
    const index = tasks.value.findIndex((item) => item.taskId === taskId)
    if (index >= 0) tasks.value[index] = task
    else tasks.value.unshift(task)
    return task
  }

  async function loadTasks(): Promise<void> {
    tasks.value = await run(() => motionDataService.listTasks())
  }

  async function loadTaskResult(taskId: string): Promise<TaskResult> {
    const cached = taskResults.value[taskId]
    if (cached) return cached
    const result = await run(() => motionDataService.getTaskResult({ taskId }))
    taskResults.value[taskId] = result
    return result
  }

  function resultById(taskId: string): TaskResult | null {
    return taskResults.value[taskId] ?? null
  }

  return {
    currentTask,
    tasks,
    taskResults,
    loading,
    errorMessage,
    completedCount,
    abnormalCount,
    averageScore,
    loadCurrentTask,
    startTask,
    finishTask,
    loadTasks,
    loadTaskResult,
    resultById,
  }
})

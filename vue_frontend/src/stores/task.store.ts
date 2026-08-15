import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { motionDataService } from '@/services/data.service'
import type { ParticipantProfile, TaskResult, TaskSummary } from '@/types/task'

export const useTaskStore = defineStore('task', () => {
  const currentTask = ref<TaskSummary | null>(null)
  const tasks = ref<TaskSummary[]>([])
  const taskResults = ref<Record<string, TaskResult>>({})
  const participants = ref<ParticipantProfile[]>([])
  let currentTaskRequest = 0

  const completedCount = computed(() => tasks.value.filter((task) => task.status === 'completed').length)
  const averageScore = computed(() => {
    const scores = tasks.value
      .map((task) => task.totalScore)
      .filter((score): score is number => score !== null)
    if (!scores.length) return null
    return scores.reduce((sum, score) => sum + score, 0) / scores.length
  })

  async function loadCurrentTask(deviceId: number): Promise<void> {
    const requestId = ++currentTaskRequest
    const task = await motionDataService.getCurrentTask({ deviceId })
    if (requestId === currentTaskRequest) currentTask.value = task
  }

  async function startTask(deviceId: number, profile: ParticipantProfile, note: string): Promise<TaskSummary> {
    currentTaskRequest += 1
    const task = await motionDataService.startTask({
      deviceId,
      participantId: profile.participantId,
      participantProfile: profile,
      note,
    })
    currentTask.value = task
    tasks.value = [task, ...tasks.value.filter((item) => item.taskId !== task.taskId)]
    return task
  }

  async function loadParticipants(): Promise<void> {
    participants.value = await motionDataService.listParticipants()
  }

  async function finishTask(taskId: string): Promise<TaskSummary> {
    currentTaskRequest += 1
    const task = await motionDataService.finishTask({ taskId })
    currentTask.value = null
    const index = tasks.value.findIndex((item) => item.taskId === taskId)
    if (index >= 0) tasks.value[index] = task
    else tasks.value.unshift(task)
    delete taskResults.value[taskId]
    return task
  }

  async function loadTasks(): Promise<void> {
    tasks.value = await motionDataService.listTasks()
  }

  async function deleteTask(taskId: string): Promise<void> {
    await motionDataService.deleteTask({ taskId })
    tasks.value = tasks.value.filter((task) => task.taskId !== taskId)
    delete taskResults.value[taskId]
    if (currentTask.value?.taskId === taskId) currentTask.value = null
  }

  async function loadTaskResult(taskId: string): Promise<TaskResult> {
    const result = await motionDataService.getTaskResult({ taskId })
    taskResults.value[taskId] = result
    return result
  }

  async function exportTask(taskId: string) {
    return motionDataService.exportTask({ taskId })
  }

  function resultById(taskId: string): TaskResult | null {
    return taskResults.value[taskId] ?? null
  }

  return {
    currentTask,
    tasks,
    participants,
    completedCount,
    averageScore,
    loadCurrentTask,
    startTask,
    loadParticipants,
    finishTask,
    loadTasks,
    deleteTask,
    loadTaskResult,
    exportTask,
    resultById,
  }
})

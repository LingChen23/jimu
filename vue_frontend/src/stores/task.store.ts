import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { motionDataService } from '@/services/data.service'
import type { ParticipantProfile, TaskResult, TaskSummary } from '@/types/task'

export const useTaskStore = defineStore('task', () => {
  const currentTask = ref<TaskSummary | null>(null)
  const tasks = ref<TaskSummary[]>([])
  const taskResults = ref<Record<string, TaskResult>>({})
  const participants = ref<ParticipantProfile[]>([])
  const cloudLoading = ref(false)
  const cloudLoadedCount = ref(0)
  const cloudTotal = ref(0)
  let currentTaskRequest = 0
  let cloudTaskRequest = 0
  const taskResultRequests = new Map<string, Promise<TaskResult>>()
  const cloudDeletedTasks = new Set<string>()

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

  async function updateTaskParticipant(
    taskId: string,
    profile: ParticipantProfile,
  ): Promise<TaskSummary> {
    const task = await motionDataService.updateTaskParticipant({
      taskId,
      participantProfile: profile,
    })
    const taskIndex = tasks.value.findIndex((item) => item.taskId === taskId)
    if (taskIndex >= 0) tasks.value[taskIndex] = task
    if (currentTask.value?.taskId === taskId) currentTask.value = task
    const cachedResult = taskResults.value[taskId]
    if (cachedResult) taskResults.value[taskId] = { ...cachedResult, task }
    const savedProfile = task.participantSnapshot
    if (savedProfile) {
      participants.value = [
        savedProfile,
        ...participants.value.filter((item) => item.participantId !== savedProfile.participantId),
      ]
    }
    return task
  }

  async function loadTasks(): Promise<void> {
    cloudDeletedTasks.clear()
    tasks.value = await motionDataService.listTasks()
    cloudLoadedCount.value = 0
    cloudTotal.value = 0
  }

  async function mergeCloudTasks(): Promise<void> {
    const requestId = ++cloudTaskRequest
    const batchSize = 10
    let page = 1
    let loaded = 0
    cloudLoading.value = true
    cloudLoadedCount.value = 0
    cloudTotal.value = 0
    try {
      while (requestId === cloudTaskRequest) {
        const remote = await motionDataService.listCloudTasks(page, batchSize)
        if (requestId !== cloudTaskRequest) return
        const merged = new Map(tasks.value.map(task => [task.taskId, task]))
        remote.tasks.forEach((task) => {
          if (cloudDeletedTasks.has(task.taskId)) return
          const existing = merged.get(task.taskId)
          merged.set(task.taskId, existing
            ? { ...existing, cloudArchived: true }
            : task)
        })
        tasks.value = [...merged.values()]
          .sort((left, right) => right.startTime.localeCompare(left.startTime))
        void prefetchTaskResults(tasks.value.slice(0, 5).map(task => task.taskId))
        loaded += remote.tasks.length
        cloudLoadedCount.value = loaded
        cloudTotal.value = remote.total
        if (!remote.tasks.length || loaded >= remote.total) return
        page += 1
      }
    } finally {
      if (requestId === cloudTaskRequest) cloudLoading.value = false
    }
  }

  function cancelCloudTaskLoading(): void {
    cloudTaskRequest += 1
    cloudLoading.value = false
  }

  async function deleteTask(taskId: string, scope: 'local' | 'cloud', keepCloudRecord = false): Promise<void> {
    const existing = tasks.value.find((task) => task.taskId === taskId)
    await motionDataService.deleteTask({ taskId, scope })
    const keep = existing && (scope === 'local'
      ? existing.cloudArchived || keepCloudRecord
      : existing.localAvailable !== false)
    if (scope === 'cloud') {
      cloudDeletedTasks.add(taskId)
      if (existing?.cloudArchived) cloudTotal.value = Math.max(0, cloudTotal.value - 1)
    }
    tasks.value = keep
      ? tasks.value.map((task) => task.taskId === taskId
        ? scope === 'local'
          ? { ...task, localAvailable: false, cloudArchived: true }
          : { ...task, cloudArchived: false }
        : task)
      : tasks.value.filter((task) => task.taskId !== taskId)
    forgetTaskResult(taskId)
    if (scope === 'local' && currentTask.value?.taskId === taskId) currentTask.value = null
  }

  function forgetTaskResult(taskId: string): void {
    delete taskResults.value[taskId]
    taskResultRequests.delete(taskId)
  }

  async function loadTaskResult(taskId: string): Promise<TaskResult> {
    const cached = taskResults.value[taskId]
    if (cached) return cached
    const pending = taskResultRequests.get(taskId)
    if (pending) return pending
    const request = motionDataService.getTaskResult({ taskId })
      .then((result) => {
        if (taskResultRequests.get(taskId) === request) taskResults.value[taskId] = result
        return result
      })
      .finally(() => {
        if (taskResultRequests.get(taskId) === request) taskResultRequests.delete(taskId)
      })
    taskResultRequests.set(taskId, request)
    return request
  }

  async function prefetchTaskResults(taskIds: string[]): Promise<void> {
    await Promise.allSettled([...new Set(taskIds)].slice(0, 5).map(loadTaskResult))
  }

  async function exportTask(taskId: string) {
    return motionDataService.exportTask({ taskId })
  }

  async function exportTasks(taskIds: string[]) {
    return motionDataService.exportTasks({ taskIds })
  }

  function resultById(taskId: string): TaskResult | null {
    return taskResults.value[taskId] ?? null
  }

  return {
    currentTask,
    tasks,
    participants,
    cloudLoading,
    cloudLoadedCount,
    cloudTotal,
    completedCount,
    averageScore,
    loadCurrentTask,
    startTask,
    loadParticipants,
    finishTask,
    updateTaskParticipant,
    loadTasks,
    mergeCloudTasks,
    cancelCloudTaskLoading,
    deleteTask,
    forgetTaskResult,
    loadTaskResult,
    prefetchTaskResults,
    exportTask,
    exportTasks,
    resultById,
  }
})

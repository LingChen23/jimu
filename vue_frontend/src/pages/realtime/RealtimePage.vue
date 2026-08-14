<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useAppStore } from '@/stores/app'
import { useMotionStore } from '@/stores/motion.store'
import { useTaskStore } from '@/stores/task.store'
import RealtimeDevicePanel from './components/RealtimeDevicePanel.vue'
import RealtimeTelemetryPanel from './components/RealtimeTelemetryPanel.vue'
import TaskCreateModal from './components/TaskCreateModal.vue'
import { isTaskBoundToDevice } from './realtime.view-model'
import type { ParticipantProfile } from '@/types/task'
import type { MovementDetail } from '@/types/motion'

const appStore = useAppStore()
const motionStore = useMotionStore()
const taskStore = useTaskStore()
const switching = ref(false)
const modal = ref(false)
const emptyProfile = (): ParticipantProfile => ({
  participantId: '', name: '', gender: '', age: null, birthDate: '',
  educationLevel: '', dominantHand: '', phone: '', visionStatus: '',
  hearingStatus: '', upperLimbStatus: '', cognitiveStatus: '', profileNote: '',
})
const participantProfile = ref<ParticipantProfile>(emptyProfile())
const note = ref('')
const taskActionLoading = ref(false)
const taskActionError = ref<string | null>(null)
const completedMovementDetails = ref<MovementDetail[]>([])
const resultLoading = ref(false)
let selectionRequest = 0
let liveTrajectoryTimer: number | null = null
let taskRefreshTimer: number | null = null
let deviceStatusTimer: number | null = null
let liveTrajectoryRefreshRunning = false
let taskRefreshRunning = false
let deviceStatusRefreshRunning = false
let pageActive = false

const device = computed(() => motionStore.deviceById(appStore.selectedDeviceId))
const snapshot = computed(() => motionStore.snapshots[appStore.selectedDeviceId] ?? null)
const realtime = computed(() => motionStore.realtimeData[appStore.selectedDeviceId] ?? null)
const isTested = computed(() => device.value?.integrationStatus === 'tested')
const selectedTask = computed(() => (
  (taskStore.currentTask?.deviceIds ?? [taskStore.currentTask?.deviceId])
    .includes(appStore.selectedDeviceId) ? taskStore.currentTask : null
))
const taskBindingMismatch = computed(() => Boolean(selectedTask.value && device.value
  && !isTaskBoundToDevice(selectedTask.value, device.value)))
const currentTask = computed(() => (
  isTaskBoundToDevice(selectedTask.value, device.value) ? selectedTask.value : null
))
const completedTask = computed(() => taskStore.latestCompletedTask)
const canStart = computed(() => Boolean(isTested.value && !selectedTask.value && !switching.value && !taskActionLoading.value))
const canFinish = computed(() => Boolean(
  currentTask.value?.status === 'collecting' && !taskBindingMismatch.value && !switching.value && !taskActionLoading.value,
))
function clearRealtimeTimer(): void {
  if (liveTrajectoryTimer !== null) window.clearInterval(liveTrajectoryTimer)
  liveTrajectoryTimer = null
}

function clearTaskRefreshTimer(): void {
  if (taskRefreshTimer !== null) window.clearInterval(taskRefreshTimer)
  taskRefreshTimer = null
}

function clearDeviceStatusTimer(): void {
  if (deviceStatusTimer !== null) window.clearInterval(deviceStatusTimer)
  deviceStatusTimer = null
}

async function refreshAllDeviceStates(): Promise<void> {
  if (!pageActive || deviceStatusRefreshRunning || switching.value) return
  deviceStatusRefreshRunning = true
  try {
    await motionStore.refreshDeviceStatus()
  } catch {
    // Preserve the last valid state; the next poll retries automatically.
  } finally {
    deviceStatusRefreshRunning = false
  }
}

async function refreshCurrentTask(): Promise<void> {
  if (!pageActive || taskRefreshRunning || switching.value || taskActionLoading.value || !taskStore.currentTask) return
  taskRefreshRunning = true
  try {
    await taskStore.loadCurrentTask(appStore.selectedDeviceId)
  } catch {
    // Keep the last valid task state; the next low-frequency poll retries.
  } finally {
    taskRefreshRunning = false
  }
}

async function refreshLiveTrajectory(): Promise<void> {
  if (!pageActive || liveTrajectoryRefreshRunning || switching.value || !isTested.value) return
  liveTrajectoryRefreshRunning = true
  try {
    await motionStore.refreshRealtimeData(appStore.selectedDeviceId)
  } catch {
    // The next poll retries automatically; the last valid trajectory remains visible.
  } finally {
    liveTrajectoryRefreshRunning = false
  }
}

function startRealtimeTimer(): void {
  clearRealtimeTimer()
  clearTaskRefreshTimer()
  clearDeviceStatusTimer()
  if (!pageActive) return
  liveTrajectoryTimer = window.setInterval(() => void refreshLiveTrajectory(), 40)
  taskRefreshTimer = window.setInterval(() => void refreshCurrentTask(), 500)
  deviceStatusTimer = window.setInterval(() => void refreshAllDeviceStates(), 200)
}

async function loadSelectedDevice(deviceId: number): Promise<void> {
  const requestId = ++selectionRequest
  appStore.selectDevice(deviceId)
  taskActionError.value = null
  const nextDevice = motionStore.deviceById(deviceId)
  if (nextDevice?.integrationStatus === 'pending-integration') {
    switching.value = false
    return
  }
  switching.value = true
  try {
    await Promise.all([
      motionStore.loadRealtimeContext(deviceId),
      taskStore.loadCurrentTask(deviceId),
    ])
  } catch (error) {
    if (requestId === selectionRequest) {
      taskActionError.value = error instanceof Error ? error.message : String(error)
    }
  } finally {
    if (requestId === selectionRequest) switching.value = false
  }
}

onMounted(async () => {
  pageActive = true
  try {
    await motionStore.loadDevices()
    const initialDeviceId = motionStore.deviceById(appStore.selectedDeviceId)?.deviceId
      ?? motionStore.devices[0]?.deviceId
    if (initialDeviceId) await loadSelectedDevice(initialDeviceId)
    if (!taskStore.currentTask) {
      await taskStore.loadTasks()
      if (completedTask.value) await loadCompletedTaskDetails(completedTask.value)
    }
  } catch (error) {
    if (pageActive) taskActionError.value = error instanceof Error ? error.message : String(error)
  } finally {
    if (pageActive) startRealtimeTimer()
  }
})

onBeforeUnmount(() => {
  pageActive = false
  selectionRequest += 1
  clearRealtimeTimer()
  clearTaskRefreshTimer()
  clearDeviceStatusTimer()
})

function openTaskModal(): void {
  if (!canStart.value) return
  participantProfile.value = emptyProfile()
  note.value = ''
  modal.value = true
  void taskStore.loadParticipants()
}

async function startTask(): Promise<void> {
  if (!device.value || !participantProfile.value.participantId.trim()) return
  taskActionLoading.value = true
  taskActionError.value = null
  try {
    await taskStore.startTask(device.value.deviceId, participantProfile.value, note.value.trim())
    completedMovementDetails.value = []
    modal.value = false
  } catch (error) {
    taskActionError.value = error instanceof Error ? error.message : String(error)
  } finally {
    taskActionLoading.value = false
  }
}

async function finishAndViewResult(): Promise<void> {
  const taskId = currentTask.value?.taskId
  if (!taskId) return
  taskActionLoading.value = true
  resultLoading.value = true
  taskActionError.value = null
  try {
    const finished = await taskStore.finishTask(taskId)
    await nextTick()
    document.getElementById('realtime-collection-result')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    await loadCompletedTaskDetails(finished)
  } catch (error) {
    taskActionError.value = error instanceof Error ? error.message : String(error)
  } finally {
    resultLoading.value = false
    taskActionLoading.value = false
  }
}

async function loadCompletedTaskDetails(task: import('@/types/task').TaskSummary): Promise<void> {
  resultLoading.value = true
  try {
    completedMovementDetails.value = (await Promise.allSettled(
      (task.movementReferences ?? []).map((reference) => motionStore.loadMovementDetail(
        reference.deviceId,
        reference.movementId,
        reference.analysisMode,
        task.taskId,
      )),
    )).flatMap((result) => result.status === 'fulfilled' ? [result.value] : [])
  } finally {
    resultLoading.value = false
  }
}
</script>

<template>
  <div v-if="device" class="page-stack realtime-page">
    <section class="page-heading realtime-heading">
      <h1>实时采集</h1>
      <div class="heading-actions">
        <button class="btn secondary" :disabled="!canStart" @click="openTaskModal">{{ taskActionLoading ? '处理中…' : '开始采集' }}</button>
        <button class="btn primary" :disabled="!canFinish" @click="finishAndViewResult">{{ taskActionLoading ? '正在结束…' : '结束采集并查看结果' }}</button>
      </div>
    </section>

    <section v-if="taskActionError" class="card task-action-error">{{ taskActionError }}</section>

    <RealtimeDevicePanel
      :devices="motionStore.devices"
      :selected-device-id="appStore.selectedDeviceId"
      :device="device"
      :current-task="currentTask"
      :task-binding-mismatch="taskBindingMismatch"
      :switching="switching"
      @select="loadSelectedDevice"
    />

    <RealtimeTelemetryPanel
      :is-tested="isTested"
      :loading="switching"
      :snapshot="snapshot"
      :realtime="realtime"
      :current-task="currentTask"
      :completed-task="completedTask"
      :completed-movement-details="completedMovementDetails"
      :result-loading="resultLoading"
    />

    <TaskCreateModal
      v-if="modal"
      v-model:profile="participantProfile"
      v-model:note="note"
      :participants="taskStore.participants"
      @close="modal = false"
      @confirm="startTask"
    />
  </div>
</template>

<style scoped>
.realtime-page{display:flex;min-height:calc(var(--app-viewport-height) - 48px);flex-direction:column;gap:12px}.realtime-heading{flex:0 0 auto;padding:0}.realtime-heading .btn:disabled{cursor:not-allowed;opacity:.5}.task-action-error{padding:10px 14px;border-color:#f2caca;background:#fff8f8;color:#b53f3f;font-size:12px}@media(max-width:1000px){.realtime-page{min-height:0}}@media(max-width:760px){.realtime-heading{align-items:flex-start;flex-direction:column}.heading-actions{width:100%;flex-wrap:wrap}.heading-actions .btn{flex:1}}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useMotionStore } from '@/stores/motion.store'
import { useTaskStore } from '@/stores/task.store'
import RealtimeDevicePanel from './components/RealtimeDevicePanel.vue'
import RealtimeTelemetryPanel from './components/RealtimeTelemetryPanel.vue'
import TaskCreateModal from './components/TaskCreateModal.vue'
import { isTaskBoundToDevice } from './realtime.view-model'

const appStore = useAppStore()
const motionStore = useMotionStore()
const taskStore = useTaskStore()
const router = useRouter()
const switching = ref(false)
const modal = ref(false)
const participant = ref('')
const note = ref('')
const taskActionLoading = ref(false)
const taskActionError = ref<string | null>(null)
let selectionRequest = 0
let realtimeTimer: number | null = null
let realtimeRefreshRunning = false
let liveTrajectoryTimer: number | null = null
let liveTrajectoryRefreshRunning = false

const device = computed(() => motionStore.deviceById(appStore.selectedDeviceId))
const snapshot = computed(() => motionStore.snapshots[appStore.selectedDeviceId] ?? null)
const realtime = computed(() => motionStore.realtimeData[appStore.selectedDeviceId] ?? null)
const isTested = computed(() => device.value?.integrationStatus === 'tested')
const selectedTask = computed(() => (
  taskStore.currentTask?.deviceId === appStore.selectedDeviceId ? taskStore.currentTask : null
))
const taskBindingMismatch = computed(() => Boolean(selectedTask.value && device.value
  && !isTaskBoundToDevice(selectedTask.value, device.value)))
const currentTask = computed(() => (
  isTaskBoundToDevice(selectedTask.value, device.value) ? selectedTask.value : null
))
const canStart = computed(() => Boolean(isTested.value && !selectedTask.value && !switching.value && !taskActionLoading.value))
const canFinish = computed(() => Boolean(
  currentTask.value?.status === 'collecting' && !taskBindingMismatch.value && !switching.value && !taskActionLoading.value,
))
const motionText = computed(() => ({
  stationary: '静止',
  moving: '移动中',
  calibrating: '校准中',
  unknown: '未知',
})[snapshot.value?.motionState ?? device.value?.motionState ?? 'unknown'])

function clearRealtimeTimer(): void {
  if (realtimeTimer !== null) window.clearInterval(realtimeTimer)
  realtimeTimer = null
  if (liveTrajectoryTimer !== null) window.clearInterval(liveTrajectoryTimer)
  liveTrajectoryTimer = null
}

async function refreshLiveTrajectory(): Promise<void> {
  if (liveTrajectoryRefreshRunning || switching.value || !isTested.value) return
  liveTrajectoryRefreshRunning = true
  try {
    await motionStore.refreshRealtimeData(appStore.selectedDeviceId)
  } catch {
    // The next poll retries automatically; the last valid trajectory remains visible.
  } finally {
    liveTrajectoryRefreshRunning = false
  }
}

async function refreshRealtimeSnapshot(): Promise<void> {
  if (realtimeRefreshRunning || switching.value || !isTested.value) return
  realtimeRefreshRunning = true
  try {
    await motionStore.refreshMotionSnapshot(appStore.selectedDeviceId)
  } catch {
    // The next poll retries automatically; the last valid frame remains visible.
  } finally {
    realtimeRefreshRunning = false
  }
}

function startRealtimeTimer(): void {
  clearRealtimeTimer()
  realtimeTimer = window.setInterval(() => void refreshRealtimeSnapshot(), 500)
  liveTrajectoryTimer = window.setInterval(() => void refreshLiveTrajectory(), 80)
}

async function loadSelectedDevice(deviceId: number): Promise<void> {
  const requestId = ++selectionRequest
  appStore.selectDevice(deviceId)
  appStore.globalNotice = null
  taskActionError.value = null
  const nextDevice = motionStore.deviceById(deviceId)
  if (nextDevice?.integrationStatus === 'pending-integration') {
    switching.value = false
    return
  }
  switching.value = true
  try {
    await Promise.all([
      motionStore.loadDeviceContext(deviceId),
      taskStore.loadCurrentTask(deviceId),
    ])
  } finally {
    if (requestId === selectionRequest) switching.value = false
  }
}

onMounted(async () => {
  await motionStore.loadDevices()
  const initialDeviceId = motionStore.deviceById(appStore.selectedDeviceId)?.deviceId
    ?? motionStore.devices[0]?.deviceId
  if (initialDeviceId) await loadSelectedDevice(initialDeviceId)
  startRealtimeTimer()
})

onBeforeUnmount(() => {
  clearRealtimeTimer()
})

function openTaskModal(): void {
  if (!canStart.value) return
  appStore.globalNotice = null
  participant.value = ''
  note.value = ''
  modal.value = true
}

async function startTask(): Promise<void> {
  if (!device.value || !participant.value.trim()) return
  taskActionLoading.value = true
  taskActionError.value = null
  try {
    await taskStore.startTask(device.value.deviceId, participant.value.trim(), note.value.trim())
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
  taskActionError.value = null
  try {
    await taskStore.finishTask(taskId)
    await router.push(`/results/${taskId}`)
  } catch (error) {
    taskActionError.value = error instanceof Error ? error.message : String(error)
  } finally {
    taskActionLoading.value = false
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
      :motion-label="motionText"
    />

    <TaskCreateModal
      v-if="modal"
      v-model:participant="participant"
      v-model:note="note"
      @close="modal = false"
      @confirm="startTask"
    />
  </div>
</template>

<style scoped>
.realtime-page{display:flex;min-height:calc(var(--app-viewport-height) - 48px);flex-direction:column;gap:12px}.realtime-heading{flex:0 0 auto;padding:0}.realtime-heading .btn:disabled{cursor:not-allowed;opacity:.5}.task-action-error{padding:10px 14px;border-color:#f2caca;background:#fff8f8;color:#b53f3f;font-size:12px}@media(max-width:1000px){.realtime-page{min-height:0}}@media(max-width:760px){.realtime-heading{align-items:flex-start;flex-direction:column}.heading-actions{width:100%;flex-wrap:wrap}.heading-actions .btn{flex:1}}
</style>

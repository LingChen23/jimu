<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useMotionStore } from '@/stores/motion.store'
import { useTaskStore } from '@/stores/task.store'
import type { AnalysisMode, MovementExportFormat, MovementRecord } from '@/types/motion'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import ResultDiagnosticsSection from './components/ResultDiagnosticsSection.vue'
import ResultFeatureSummary from './components/ResultFeatureSummary.vue'
import ResultMotionSection from './components/ResultMotionSection.vue'
import ResultOverview from './components/ResultOverview.vue'
import ResultQualitySection from './components/ResultQualitySection.vue'
import ResultTrajectorySection from './components/ResultTrajectorySection.vue'
import { parseResultRoute } from './result-route'

interface MovementReference {
  deviceId: number
  movementId: number
  analysisMode: AnalysisMode
}

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const motionStore = useMotionStore()
const taskStore = useTaskStore()
const movementReference = ref<MovementReference | null>(null)
const pageLoading = ref(true)
const pageError = ref<string | null>(null)
const enrichmentNotice = ref<string | null>(null)
const taskUsesMockMovement = ref(false)
const exporting = ref<MovementExportFormat | null>(null)

const isTaskResult = computed(() => route.name === 'task-result')
const isMovementResult = computed(() => route.name === 'movement-result')
const resultKind = computed<'task' | 'movement'>(() => isTaskResult.value ? 'task' : 'movement')
const taskId = computed(() => {
  try {
    const reference = parseResultRoute(route.name, route.params)
    return reference.kind === 'task' ? reference.taskId : ''
  } catch {
    return ''
  }
})
const taskResult = computed(() => taskId.value ? taskStore.resultById(taskId.value) : null)
const task = computed(() => taskResult.value?.task ?? null)
const movementDetail = computed(() => {
  const reference = movementReference.value
  return reference
    ? motionStore.movementDetailByKey(reference.deviceId, reference.movementId, reference.analysisMode)
    : null
})
const deviceId = computed(() => task.value?.deviceId ?? movementReference.value?.deviceId ?? null)
const device = computed(() => deviceId.value === null ? null : motionStore.deviceById(deviceId.value))
const statistics = computed(() => movementDetail.value?.statistics
  ?? (taskResult.value?.score.demoOnly ? taskResult.value.statistics : null))
const acceleration = computed(() => movementDetail.value?.acceleration ?? taskResult.value?.acceleration ?? [])
const gyroscope = computed(() => movementDetail.value?.gyroscope ?? taskResult.value?.gyroscope ?? [])
const events = computed(() => movementDetail.value?.events ?? taskResult.value?.events ?? [])
const speed = computed(() => movementDetail.value?.speed ?? null)
const orientation = computed(() => movementDetail.value?.orientation ?? null)
const trajectories = computed(() => movementDetail.value?.trajectories ?? null)
const tremor = computed(() => movementDetail.value?.tremor ?? null)
const sessionTremor = computed(() => movementDetail.value?.sessionTremorAverage ?? null)

function latestCompletedMovement(records: MovementRecord[]): MovementRecord | null {
  const completed = records
    .filter((record) => record.state === 'completed')
    .sort((left, right) => right.startedAt.localeCompare(left.startedAt))
  return completed.find((record) => record.analysisMode === 'pc') ?? completed[0] ?? null
}

async function loadRouteResult(): Promise<void> {
  pageLoading.value = true
  pageError.value = null
  enrichmentNotice.value = null
  movementReference.value = null
  taskUsesMockMovement.value = false
  try {
    const routeReference = parseResultRoute(route.name, route.params)
    if (routeReference.kind === 'task') {
      const [loadedResult] = await Promise.all([
        taskStore.loadTaskResult(routeReference.taskId),
        motionStore.devices.length ? Promise.resolve() : motionStore.loadDevices(),
      ])
      appStore.selectDevice(loadedResult.task.deviceId)
      try {
        await motionStore.loadMovementRecords(loadedResult.task.deviceId)
        const candidate = loadedResult.movementReference
          ?? (loadedResult.task.movementReferences === undefined
            ? latestCompletedMovement(motionStore.movementRecords[loadedResult.task.deviceId] ?? [])
            : null)
        if (candidate) {
          movementReference.value = {
            deviceId: candidate.deviceId,
            movementId: candidate.movementId,
            analysisMode: candidate.analysisMode,
          }
          await motionStore.loadMovementDetail(candidate.deviceId, candidate.movementId, candidate.analysisMode)
        } else {
          enrichmentNotice.value = '当前任务设备没有可用于补充高级字段的完整移动记录。'
        }
      } catch (error) {
        enrichmentNotice.value = `高级移动字段暂不可用：${error instanceof Error ? error.message : String(error)}`
      }
    } else {
      movementReference.value = routeReference
      await Promise.all([
        motionStore.devices.length ? Promise.resolve() : motionStore.loadDevices(),
        motionStore.loadMovementDetail(
          routeReference.deviceId,
          routeReference.movementId,
          routeReference.analysisMode,
        ),
      ])
      appStore.selectDevice(routeReference.deviceId)
    }
  } catch (error) {
    pageError.value = error instanceof Error ? error.message : String(error)
  } finally {
    pageLoading.value = false
  }
}

watch(
  () => [route.name, route.params.taskId, route.params.deviceId, route.params.analysisMode, route.params.movementId],
  () => { void loadRouteResult() },
  { immediate: true },
)

async function downloadMovement(format: MovementExportFormat): Promise<void> {
  const reference = movementReference.value
  if (!reference || !movementDetail.value) return
  exporting.value = format
  pageError.value = null
  try {
    const exported = await motionStore.exportMovement(
      reference.deviceId,
      reference.movementId,
      format,
      reference.analysisMode,
    )
    const blob = new Blob([exported.content], { type: exported.mimeType })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = exported.fileName
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (error) {
    pageError.value = `导出失败：${error instanceof Error ? error.message : String(error)}`
  } finally {
    exporting.value = null
  }
}
function goRealtime(): void {
  if (deviceId.value !== null) appStore.selectDevice(deviceId.value)
  void router.push('/realtime')
}
</script>

<template>
  <div class="page-stack result-page">
    <section class="page-heading result-heading">
      <div>
        <div class="result-eyebrow"><StatusBadge tone="green" :text="isTaskResult ? '采集任务' : '移动记录'" /> {{ isTaskResult ? '任务结果' : isMovementResult ? '移动结果' : '未知结果' }}</div>
        <h1>结果详情</h1>
        <p>详情类型由具名路由确定；通过实时采集结束或历史记录进入。</p>
      </div>
      <div class="heading-actions result-actions">
        <button class="btn secondary" :disabled="!movementDetail || exporting !== null" @click="downloadMovement('csv')">{{ exporting === 'csv' ? '导出中…' : '导出 CSV' }}</button>
        <button class="btn secondary" :disabled="!movementDetail || exporting !== null" @click="downloadMovement('json')">{{ exporting === 'json' ? '导出中…' : '导出 JSON' }}</button>
        <button class="btn secondary" @click="router.push('/history')">返回历史记录</button>
        <button class="btn primary" :disabled="pageLoading || deviceId === null" @click="goRealtime">开始新任务</button>
      </div>
    </section>

    <section v-if="pageLoading" class="card result-state"><strong>正在加载结果详情</strong><span>动态数据正在通过 Motion/Task Store 获取。</span></section>
    <section v-else-if="pageError" class="card result-state result-state--error"><strong>结果详情不可用</strong><span>{{ pageError }}</span><button class="btn secondary" @click="router.push('/history')">返回历史记录</button></section>

    <template v-else-if="taskResult || movementDetail">
      <ResultOverview
        :result-kind="resultKind"
        :task-result="taskResult"
        :task="task"
        :device="device"
        :movement="movementDetail"
        :uses-mock-movement="taskUsesMockMovement"
        :enrichment-notice="enrichmentNotice"
      />
      <ResultFeatureSummary :statistics="statistics" :speed="speed" :tremor="tremor" />
      <ResultMotionSection :statistics="statistics" :acceleration="acceleration" :gyroscope="gyroscope" />
      <ResultDiagnosticsSection :speed="speed" :events="events" :orientation="orientation" />
      <ResultTrajectorySection :trajectories="trajectories" />
      <ResultQualitySection :tremor="tremor" :session-tremor="sessionTremor" />
    </template>

    <section v-else class="card result-state"><strong>没有可展示的结果</strong><span>请返回历史记录重新选择。</span></section>
  </div>
</template>

<style scoped>
.result-page{gap:16px}.result-eyebrow{display:flex;align-items:center;gap:9px;margin-bottom:9px;color:#7b879a;font-size:12px}.result-actions{flex-wrap:wrap;justify-content:flex-end}.result-actions button:disabled{cursor:not-allowed;opacity:.5}.result-state{min-height:180px;padding:28px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:#68758b;text-align:center}.result-state strong{color:#354259;font-size:18px}.result-state--error{border-color:#f2caca;background:#fff8f8;color:#b53f3f}.result-state--error .btn{margin-top:8px}@media(max-width:1000px){.result-heading{align-items:flex-start;flex-direction:column}.result-actions{justify-content:flex-start}}@media(max-width:680px){.result-actions{width:100%}.result-actions .btn{flex:1}}
</style>

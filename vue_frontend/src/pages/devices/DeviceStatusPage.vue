<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useMotionStore } from '@/stores/motion.store'
import type { BlockMapping, DeviceSnapshot } from '@/types/device'
import type { DeviceCommand } from '@/types/serial'
import AggregatorStatusPanel from './components/AggregatorStatusPanel.vue'
import BlockMappingPanel from './components/BlockMappingPanel.vue'
import DeviceCommandPanel from './components/DeviceCommandPanel.vue'
import DeviceDetailPanel from './components/DeviceDetailPanel.vue'
import DeviceTable from './components/DeviceTable.vue'
import SerialControlPanel from './components/SerialControlPanel.vue'

const appStore = useAppStore()
const motionStore = useMotionStore()
const router = useRouter()
const selectedId = ref<number | null>(null)
const pageLoading = ref(true)
const pageError = ref<string | null>(null)
const operationMessage = ref<string | null>(null)
const operationError = ref<string | null>(null)
const detailLoading = ref(false)
const serialLoading = ref(false)
const mappingSaving = ref(false)
const commandBusy = ref(false)
const batteryRefreshing = ref(false)
const counterResetBusy = ref(false)
let selectionRequest = 0
let statusPollTimer: number | null = null
let statusPollRunning = false
let pageActive = true

const selected = computed(() => motionStore.deviceById(selectedId.value ?? 0))
const selectedCommandResult = computed(() => selected.value ? motionStore.controlResults[selected.value.deviceId] ?? null : null)
const activeGatewayCount = computed(() => motionStore.serialStatus?.aggregator?.links.filter((link) => link.active).length ?? 0)

onMounted(async () => {
  pageLoading.value = true
  pageError.value = null
  try {
    await Promise.all([motionStore.loadDevices(), motionStore.loadSerial(), motionStore.loadBlockMapping()])
    const initial = motionStore.deviceById(appStore.selectedDeviceId) ?? motionStore.devices[0]
    if (initial) await selectDevice(initial)
  } catch (error) {
    pageError.value = error instanceof Error ? error.message : String(error)
  } finally {
    pageLoading.value = false
    scheduleStatusPoll(motionStore.serialStatus?.isOpen ? 250 : 1_000)
  }
})

onBeforeUnmount(() => {
  pageActive = false
  if (statusPollTimer !== null) window.clearTimeout(statusPollTimer)
  statusPollTimer = null
})

function scheduleStatusPoll(delay: number): void {
  if (!pageActive) return
  if (statusPollTimer !== null) window.clearTimeout(statusPollTimer)
  statusPollTimer = window.setTimeout(() => void pollDeviceStatus(), delay)
}

async function pollDeviceStatus(): Promise<void> {
  statusPollTimer = null
  if (!pageActive) return
  if (
    statusPollRunning
    || serialLoading.value
    || commandBusy.value
    || batteryRefreshing.value
    || counterResetBusy.value
  ) {
    scheduleStatusPoll(250)
    return
  }
  statusPollRunning = true
  try {
    await motionStore.refreshDeviceStatus()
  } catch {
    // Preserve the last valid state; the next scheduled poll retries.
  } finally {
    statusPollRunning = false
    scheduleStatusPoll(motionStore.serialStatus?.isOpen ? 500 : 1_500)
  }
}

function clearOperationState(): void {
  operationMessage.value = null
  operationError.value = null
}

async function runOperation(action: () => Promise<void>, success: string): Promise<void> {
  clearOperationState()
  try {
    await action()
    operationMessage.value = success
  } catch (error) {
    operationError.value = error instanceof Error ? error.message : String(error)
  }
}

async function selectDevice(device: DeviceSnapshot, force = false): Promise<void> {
  const requestId = ++selectionRequest
  selectedId.value = device.deviceId
  if (device.integrationStatus === 'pending-integration') {
    detailLoading.value = false
    return
  }
  if (!force && motionStore.linkDiagnostics[device.deviceId]) {
    detailLoading.value = false
    return
  }
  detailLoading.value = true
  try {
    await motionStore.loadDeviceContext(device.deviceId)
  } catch (error) {
    if (requestId === selectionRequest) operationError.value = error instanceof Error ? error.message : String(error)
  } finally {
    if (requestId === selectionRequest) detailLoading.value = false
  }
}

async function refreshSerial(): Promise<void> {
  serialLoading.value = true
  await runOperation(() => motionStore.loadSerial(), '串口列表与聚合器状态已刷新。')
  serialLoading.value = false
}

async function openSerial(port: string, baudRate: number): Promise<void> {
  serialLoading.value = true
  await runOperation(async () => {
    await motionStore.openSerial(port, baudRate)
    await Promise.all([motionStore.loadSerial(), motionStore.loadDevices()])
    if (selected.value) await selectDevice(selected.value, true)
  }, `${port} 已打开。`)
  serialLoading.value = false
  scheduleStatusPoll(250)
}

async function closeSerial(): Promise<void> {
  serialLoading.value = true
  await runOperation(async () => {
    await motionStore.closeSerial()
    await Promise.all([motionStore.loadSerial(), motionStore.loadDevices()])
  }, '串口已关闭。')
  serialLoading.value = false
  scheduleStatusPoll(1_000)
}

async function saveMapping(mapping: BlockMapping): Promise<void> {
  mappingSaving.value = true
  await runOperation(async () => {
    await motionStore.updateBlockMapping(mapping)
    if (selected.value) await selectDevice(selected.value, true)
  }, '设备与积木绑定已保存，并将应用于后续移动。')
  mappingSaving.value = false
}

async function executeCommand(command: DeviceCommand): Promise<void> {
  if (!selected.value || commandBusy.value || batteryRefreshing.value || counterResetBusy.value) return
  commandBusy.value = true
  const deviceId = selected.value.deviceId
  try {
    await runOperation(async () => {
      await motionStore.executeCommand(deviceId, command)
      await Promise.all([motionStore.loadSerial(), selectDevice(motionStore.deviceById(deviceId) ?? selected.value!, true)])
    }, `设备 ${deviceId} 命令 ${command} 已发送。`)
  } finally {
    commandBusy.value = false
  }
}

async function resetDataAndErrorCounters(): Promise<void> {
  if (!selected.value || counterResetBusy.value || commandBusy.value || batteryRefreshing.value) return
  const deviceId = selected.value.deviceId
  counterResetBusy.value = true
  try {
    await runOperation(
      () => motionStore.resetDataAndErrorCounters(deviceId),
      `设备 ${deviceId} 的数据与错误计数已全部清零。`,
    )
  } finally {
    counterResetBusy.value = false
  }
}

async function refreshBatteries(): Promise<void> {
  if (batteryRefreshing.value || commandBusy.value || counterResetBusy.value) return
  batteryRefreshing.value = true
  clearOperationState()
  try {
    const result = await motionStore.refreshBatteryVoltages()
    const skippedIds = result.results.filter((item) => !item.ok).map((item) => item.deviceId)
    operationMessage.value = skippedIds.length
      ? `电池电压获取完成：成功 ${result.succeeded} 台，跳过 ${result.skipped} 台（设备 ${skippedIds.join('、')}）。`
      : `电池电压获取完成：${result.succeeded} 台全部成功。`
  } catch (error) {
    operationError.value = error instanceof Error ? error.message : String(error)
  } finally {
    batteryRefreshing.value = false
  }
}

function refreshSelected(): void {
  if (selected.value) void selectDevice(selected.value, true)
}

function openRealtime(device: DeviceSnapshot): void {
  if (device.integrationStatus === 'pending-integration') return
  appStore.selectDevice(device.deviceId)
  void router.push('/realtime')
}
</script>

<template>
  <div class="page-stack device-status-page">
    <section class="page-heading">
      <div>
        <h1>设备状态</h1>
        <p>管理 10 块设备、三路聚合器及积木绑定。</p>
      </div>
    </section>

    <section class="summary-grid">
      <div class="card summary"><span>设备总数</span><b>{{ motionStore.devices.length }}</b><small>设备 1～10</small></div>
      <div class="card summary"><span>在线</span><b>{{ motionStore.onlineCount }}</b><small>警告 {{ motionStore.warningCount }} 台</small></div>
      <div class="card summary"><span>离线</span><b>{{ motionStore.offlineCount }}</b><small>以实时数据为准</small></div>
      <div class="card summary"><span>聚合器链路</span><b>{{ activeGatewayCount }} / 3</b><small>{{ motionStore.serialStatus?.isOpen ? '串口已打开' : '串口未打开' }}</small></div>
    </section>

    <section v-if="pageLoading" class="card state-notice"><strong>正在加载设备状态</strong></section>
    <section v-else-if="pageError" class="card state-notice state-notice--error"><strong>设备状态加载失败</strong><span>{{ pageError }}</span></section>
    <section v-if="operationMessage || operationError" class="operation-notice" :class="{ error: operationError }">{{ operationError ?? operationMessage }}</section>

    <section class="operations-grid">
      <SerialControlPanel :ports="motionStore.serialPorts" :status="motionStore.serialStatus" :loading="serialLoading" @refresh="refreshSerial" @open="openSerial" @close="closeSerial" />
      <AggregatorStatusPanel :status="motionStore.serialStatus?.aggregator ?? null" :serial-open="motionStore.serialStatus?.isOpen ?? false" />
    </section>

    <BlockMappingPanel :mapping="motionStore.blockMapping" :saving="mappingSaving" @save="saveMapping" />

    <section class="device-workspace">
      <DeviceTable
        :devices="motionStore.devices"
        :total="motionStore.devices.length"
        :selected-id="selectedId"
        :serial-open="motionStore.serialStatus?.isOpen ?? false"
        :battery-refreshing="batteryRefreshing"
        @select="selectDevice"
        @open-realtime="openRealtime"
        @refresh-batteries="refreshBatteries"
      />
      <div v-if="selected" class="device-side-column">
        <DeviceCommandPanel :device="selected" :serial-open="motionStore.serialStatus?.isOpen ?? false" :busy="commandBusy" :result="selectedCommandResult" @command="executeCommand" />
        <DeviceDetailPanel
          :device="selected"
          :loading="detailLoading"
          :serial-open="motionStore.serialStatus?.isOpen ?? false"
          :counter-reset-busy="counterResetBusy"
          @refresh="refreshSelected"
          @open-realtime="openRealtime"
          @clear-counters="resetDataAndErrorCounters"
        />
      </div>
    </section>
  </div>
</template>

<style scoped>
.device-status-page{gap:14px}.device-eyebrow{display:flex;align-items:center;gap:9px;margin-bottom:9px;color:#7b879a;font-size:12px}.state-notice{padding:16px 18px;display:flex;gap:10px;align-items:center;color:#59667a}.state-notice span{font-size:12px}.state-notice--error{border-color:#f2caca;background:#fff8f8;color:#b53f3f}.operation-notice{padding:9px 12px;border:1px solid #cde9d8;border-radius:9px;background:#f4fbf7;color:#28794a;font-size:11px}.operation-notice.error{border-color:#f2caca;background:#fff8f8;color:#b53f3f}.operations-grid{display:grid;grid-template-columns:minmax(440px,.85fr) minmax(620px,1.15fr);gap:12px;align-items:stretch}.operations-grid>*{min-width:0;height:100%}.device-workspace{height:calc(var(--app-viewport-height, 100vh) - 48px);display:grid;grid-template-columns:minmax(720px,1.5fr) minmax(420px,.8fr);gap:12px;align-items:stretch}.device-side-column{height:100%;display:grid;grid-template-rows:auto minmax(0,1fr);gap:12px;min-width:0;min-height:0;position:sticky;top:16px;overflow:hidden}.device-workspace :deep(.device-table-card){height:100%;min-height:0;display:flex;flex-direction:column}.device-workspace :deep(.device-table-card .table-wrap){max-height:none;min-height:0;flex:1 1 auto;overflow:auto}.device-workspace :deep(.device-detail){height:100%;min-height:0;overflow:auto;scrollbar-gutter:stable}@media(max-width:1380px){.operations-grid{grid-template-columns:1fr}.device-workspace{grid-template-columns:minmax(0,1.3fr) minmax(400px,.7fr)}}@media(max-width:1100px){.device-workspace{height:auto;grid-template-columns:1fr}.device-side-column{height:auto;position:static;grid-template-rows:auto auto;overflow:visible}.device-workspace :deep(.device-table-card .table-wrap){max-height:620px}.device-workspace :deep(.device-detail){height:auto;overflow:visible}}@media(max-width:680px){.device-eyebrow{align-items:flex-start}.state-notice{align-items:flex-start;flex-direction:column}}
</style>

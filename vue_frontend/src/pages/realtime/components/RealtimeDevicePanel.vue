<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { DeviceSnapshot } from '@/types/device'
import type { TaskSummary } from '@/types/task'
import DeviceSwitcher from './DeviceSwitcher.vue'

const props = defineProps<{
  devices: DeviceSnapshot[]
  selectedDeviceId: number
  device: DeviceSnapshot
  currentTask: TaskSummary | null
  taskBindingMismatch: boolean
  switching: boolean
}>()

const emit = defineEmits<{ select: [deviceId: number] }>()

const tested = computed(() => props.device.integrationStatus === 'tested')
const taskState = computed(() => {
  if (props.taskBindingMismatch) return '绑定信息不一致'
  return props.currentTask ? '采集中' : '暂无当前任务'
})
const taskTone = computed(() => {
  if (props.taskBindingMismatch) return 'orange'
  return props.currentTask ? 'purple' : 'gray'
})
const selectedTaskBlock = computed(() => props.currentTask?.blockBindings
  ?.find((entry) => entry.deviceId === props.device.deviceId)?.blockId)

function connectionText(): string {
  return tested.value && props.device.connectionStatus !== 'offline' ? '已连接' : '未连接'
}
</script>

<template>
  <section class="card realtime-device-panel">
    <div class="realtime-section-head">
      <div class="realtime-device-title"><div class="card-title">采集积木</div><span>{{ devices.length }} 块</span></div>
      <StatusBadge :tone="tested ? 'green' : 'gray'" :text="tested ? '已实测' : '待接入'" />
    </div>
    <div class="realtime-switcher" :class="{ 'is-loading': switching }">
      <DeviceSwitcher :devices="devices" :selected-device-id="selectedDeviceId" @select="emit('select', $event)" />
    </div>

    <div v-if="taskBindingMismatch" class="realtime-binding-notice">
      当前任务中的设备与积木绑定已发生变化，请恢复任务开始时的绑定后再结束任务。
    </div>

    <div class="realtime-info-strip" aria-label="当前积木和任务绑定信息">
      <div><span>当前积木</span><strong>{{ device.displayName }}</strong></div>
      <div class="realtime-info-item--wide"><span>连接 / 任务</span><div class="realtime-status-pair"><StatusBadge :tone="tested && device.connectionStatus !== 'offline' ? 'green' : 'gray'" :text="connectionText()" /><StatusBadge :tone="taskTone" :text="taskState" /></div></div>
      <div><span>任务编号</span><strong>{{ currentTask?.taskId ?? '—' }}</strong></div>
      <div><span>体验者编号</span><strong>{{ currentTask?.participantId ?? '—' }}</strong></div>
      <div><span>积木编号</span><strong>{{ selectedTaskBlock ?? device.blockId }}</strong></div>
    </div>
  </section>
</template>

<style scoped>
.realtime-device-panel{padding:14px 16px}.realtime-section-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.realtime-device-title{display:flex;align-items:center;gap:8px}.realtime-device-title>span{color:#7b879a;font-size:10px}.realtime-switcher{margin-top:9px}.realtime-switcher.is-loading{pointer-events:none;opacity:.72}.realtime-info-strip{display:flex;margin-top:8px;overflow-x:auto;border:1px solid #e4e9f1;border-radius:10px;background:#f8fafc;scrollbar-width:thin}.realtime-info-strip>div{flex:1 0 112px;min-width:0;padding:8px 10px;border-right:1px solid #e4e9f1}.realtime-info-strip>div:last-child{border-right:0}.realtime-info-strip>.realtime-info-item--wide{flex-basis:160px}.realtime-info-strip span{display:block;color:#7b879a;font-size:9px}.realtime-info-strip strong{display:block;margin-top:4px;overflow:hidden;color:#354259;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.realtime-status-pair{display:flex;align-items:center;gap:4px;margin-top:4px;white-space:nowrap}.realtime-binding-notice{margin-top:8px;padding:8px 10px;border:1px solid #f4d39b;border-radius:8px;background:#fff8eb;color:#8d5a00;font-size:11px;line-height:1.45}@media(max-width:760px){.realtime-device-panel{padding:12px}.realtime-info-strip>div{flex-basis:118px}}
</style>

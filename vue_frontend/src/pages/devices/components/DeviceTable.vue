<script setup lang="ts">
import { blockName } from '@/config/system'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { DeviceSnapshot } from '@/types/device'
import { batteryButtonState } from '../device-operation-state'
import {
  batteryLevelText,
  batteryLevelTone,
  batteryVoltageText,
  connectionText,
  connectionTone,
  integrationText,
  usageText,
  usageTone,
} from '../device.formatters'

defineProps<{
  devices: DeviceSnapshot[]
  total: number
  selectedId: number | null
  serialOpen: boolean
  batteryRefreshing: boolean
}>()
const emit = defineEmits<{
  select: [device: DeviceSnapshot]
  openRealtime: [device: DeviceSnapshot]
  refreshBatteries: []
}>()
</script>

<template>
  <section class="card table-card device-table-card">
    <div class="section-head">
      <div><div class="card-title">积木与硬件设备列表</div><div class="card-subtitle">选择积木查看设备详情</div></div>
      <div class="table-head-actions">
        <StatusBadge tone="blue" :text="`${devices.length} / ${total} 块`" />
        <button type="button" class="btn primary battery-button" :disabled="batteryButtonState(serialOpen, batteryRefreshing).disabled" @click.stop="emit('refreshBatteries')">
          {{ batteryButtonState(serialOpen, batteryRefreshing).label }}
        </button>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>硬件设备ID</th><th>积木名称</th><th>接入状态</th><th>连接状态</th><th>使用状态</th><th>电池</th><th>操作</th></tr></thead>
        <tbody>
          <tr
            v-for="device in devices"
            :key="device.deviceId"
            :class="{ selectedRow: device.deviceId === selectedId }"
            @click="emit('select', device)"
          >
            <td>{{ device.deviceId }}</td>
            <td><button class="link" @click.stop="emit('select', device)">{{ blockName(device.blockId) }}</button></td>
            <td><StatusBadge :tone="device.integrationStatus === 'tested' ? 'green' : 'gray'" :text="integrationText(device)" /></td>
            <td><StatusBadge :tone="connectionTone(device)" :text="connectionText(device)" /></td>
            <td><StatusBadge :tone="usageTone(device)" :text="usageText(device)" /></td>
            <td><span>{{ batteryVoltageText(device) }}</span><StatusBadge class="battery-level" :tone="batteryLevelTone(device)" :text="batteryLevelText(device)" /></td>
            <td><button class="link" :disabled="device.integrationStatus === 'pending-integration'" @click.stop="emit('openRealtime', device)">{{ device.integrationStatus === 'tested' ? '实时查看' : '不可用' }}</button></td>
          </tr>
          <tr v-if="!devices.length"><td colspan="7" class="empty-cell">暂无设备</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.table-head-actions{display:flex;align-items:center;gap:9px}.battery-button{padding:8px 12px;font-size:11px}.battery-button:disabled{background:#dce3ec;color:#8b96a5;cursor:not-allowed}.device-table-card .table-wrap{max-height:680px;overflow:auto;scrollbar-gutter:stable}.device-table-card table{min-width:940px}.device-table-card th{position:sticky;top:0;z-index:1}.device-table-card tbody tr{cursor:pointer}.device-table-card tbody tr:hover{background:#f7faff}.device-table-card tbody tr.selectedRow{background:#edf5ff}.device-table-card .link:disabled{color:#9aa5b5;cursor:not-allowed}.battery-level{display:table;margin-top:5px}.empty-cell{text-align:center;color:#7b879a;padding:28px}@media(max-width:680px){.table-head-actions{align-items:flex-end;flex-direction:column}.battery-button{width:100%}}
</style>

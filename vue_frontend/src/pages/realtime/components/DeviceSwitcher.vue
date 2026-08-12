<script setup lang="ts">
import type { DeviceSnapshot } from '@/types/device'
defineProps<{ devices: DeviceSnapshot[]; selectedDeviceId: number }>()
const emit = defineEmits<{ select: [deviceId: number] }>()
function text(d: DeviceSnapshot) {
  if (d.integrationStatus === 'pending-integration') return '待接入'
  if (d.connectionStatus === 'offline') return '离线'
  return d.usageStatus === 'collecting' ? '采集中' : '在线'
}
function connectionClass(d: DeviceSnapshot): 'online' | 'offline' {
  return d.integrationStatus === 'tested' && d.connectionStatus !== 'offline' ? 'online' : 'offline'
}
</script>
<template>
  <div class="device-switcher" aria-label="采集积木选择">
    <button v-for="d in devices" :key="d.deviceId" type="button" class="device-chip" :class="[`device-chip--${connectionClass(d)}`, { selected: d.deviceId === selectedDeviceId }]" :aria-pressed="d.deviceId === selectedDeviceId" @click="emit('select', d.deviceId)">
      <strong>{{ d.displayName }}</strong><span><i :class="`dot dot--${connectionClass(d)}`"></i>{{ text(d) }}</span>
    </button>
  </div>
</template>

<style scoped>
.device-switcher{display:flex;gap:7px;overflow-x:auto;padding:2px 1px 6px;scrollbar-width:thin}.device-chip{flex:1 0 78px;min-width:78px;padding:8px 9px;border:1px solid var(--border);border-radius:9px;background:#fff;display:grid;gap:4px;text-align:left;cursor:pointer}.device-chip strong{font-size:12px;white-space:nowrap}.device-chip.selected{padding:7px 8px;border:2px solid var(--primary);background:#f4f8ff}.device-chip--offline{background:#f8f9fb;color:#8e98a8}.device-chip span{display:flex;align-items:center;gap:5px;color:var(--gray);font-size:10px;white-space:nowrap}.dot{width:7px;height:7px;border-radius:50%;display:inline-block}.dot--online{background:var(--green)}.dot--offline{background:#adb6c4}
</style>

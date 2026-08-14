<script setup lang="ts">
import type { DeviceSnapshot } from '@/types/device'
defineProps<{ devices: DeviceSnapshot[]; selectedDeviceId: number }>()
const emit = defineEmits<{ select: [deviceId: number] }>()
function text(d: DeviceSnapshot) {
  if (d.integrationStatus === 'pending-integration') return '待接入'
  if (d.connectionStatus === 'offline') return '离线'
  if (d.motionState === 'moving') return '移动中'
  return d.usageStatus === 'collecting' ? '采集中' : '在线'
}
function connectionClass(d: DeviceSnapshot): 'online' | 'offline' {
  return d.integrationStatus === 'tested' && d.connectionStatus !== 'offline' ? 'online' : 'offline'
}
</script>
<template>
  <div class="device-switcher" aria-label="采集积木选择">
    <button v-for="d in devices" :key="d.deviceId" type="button" class="device-chip" :class="[`device-chip--${connectionClass(d)}`, { selected: d.deviceId === selectedDeviceId, 'is-moving': d.motionState === 'moving' }]" :aria-pressed="d.deviceId === selectedDeviceId" @click="emit('select', d.deviceId)">
      <strong>{{ d.displayName }}</strong><span><i :class="[`dot dot--${connectionClass(d)}`, { 'dot--moving': d.motionState === 'moving' }]"></i>{{ text(d) }}</span>
    </button>
  </div>
</template>

<style scoped>
.device-switcher{display:flex;gap:7px;overflow-x:auto;padding:2px 1px 6px;scrollbar-width:thin}.device-chip{flex:1 0 78px;min-width:78px;padding:8px 9px;border:1px solid var(--border);border-radius:9px;background:#fff;display:grid;gap:4px;text-align:left;cursor:pointer;transition:border-color .15s ease,background-color .15s ease,box-shadow .15s ease,transform .15s ease}.device-chip strong{font-size:12px;white-space:nowrap}.device-chip.selected{padding:7px 8px;border:2px solid var(--primary);background:#f4f8ff}.device-chip.is-moving{padding:7px 8px;border:2px solid #f59e0b;background:#fff7e6;box-shadow:0 0 0 3px rgba(245,158,11,.14),0 6px 16px rgba(180,105,0,.14);transform:translateY(-1px)}.device-chip.is-moving strong,.device-chip.is-moving span{color:#9a5600}.device-chip--offline{background:#f8f9fb;color:#8e98a8}.device-chip span{display:flex;align-items:center;gap:5px;color:var(--gray);font-size:10px;white-space:nowrap}.dot{width:7px;height:7px;border-radius:50%;display:inline-block}.dot--online{background:var(--green)}.dot--offline{background:#adb6c4}.dot--moving{background:#f59e0b;box-shadow:0 0 0 3px rgba(245,158,11,.2);animation:moving-pulse 1s ease-in-out infinite}@keyframes moving-pulse{50%{box-shadow:0 0 0 5px rgba(245,158,11,.06)}}@media(prefers-reduced-motion:reduce){.device-chip,.dot--moving{transition:none;animation:none}}
</style>

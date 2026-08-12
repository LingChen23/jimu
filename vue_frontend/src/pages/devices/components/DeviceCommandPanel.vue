<script setup lang="ts">
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { DeviceSnapshot } from '@/types/device'
import type { DeviceCommand, DeviceCommandResult } from '@/types/serial'

defineProps<{ device: DeviceSnapshot; serialOpen: boolean; busy: boolean; result: DeviceCommandResult | null }>()
const emit = defineEmits<{ command: [command: DeviceCommand] }>()
const commands: Array<{ command: DeviceCommand; label: string; tone?: string }> = [
  { command: 'START_STREAM', label: '开始发送' }, { command: 'STOP_STREAM', label: '停止发送' },
  { command: 'GET_INFO', label: '设备信息' }, { command: 'GET_STATUS', label: '运行状态' },
  { command: 'REBOOT', label: '重启设备', tone: 'danger' },
]
</script>

<template>
  <section class="card command-panel">
    <div class="section-head"><div><div class="card-title">设备控制</div><div class="card-subtitle">设备 {{ device.deviceId }} / 积木 {{ device.blockId }}</div></div><StatusBadge :tone="serialOpen ? 'green' : 'gray'" :text="serialOpen ? '可用' : '请先打开串口'" /></div>
    <div class="command-grid"><button v-for="item in commands" :key="item.command" class="btn" :class="item.tone === 'danger' ? 'danger' : item.command === 'START_STREAM' ? 'primary' : 'secondary'" :disabled="busy || !serialOpen" @click="emit('command', item.command)">{{ item.label }}</button></div>
    <div class="command-result" :class="{ error: result && !result.ok }">{{ busy ? '正在等待设备响应…' : result ? `${result.command}：${result.message}` : '选择命令后在此显示设备响应。' }}</div>
  </section>
</template>

<style scoped>
.command-panel{padding:15px 16px}.command-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin-top:12px}.command-grid .btn{min-width:0;padding:8px 5px;font-size:11px;white-space:nowrap}.command-result{margin-top:10px;padding:9px 10px;border-radius:8px;background:#f8fafc;color:#59667a;font-size:10px;line-height:1.5}.command-result.error{background:#fff4f4;color:#b53f3f}@media(max-width:620px){.command-grid{grid-template-columns:repeat(2,1fr)}}
</style>

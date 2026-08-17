<script setup lang="ts">
import { ref, watch } from 'vue'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { SerialPortInfo, SerialStatus } from '@/types/serial'

const props = defineProps<{ ports: SerialPortInfo[]; status: SerialStatus | null; loading: boolean; counterResetBusy: boolean }>()
const emit = defineEmits<{ refresh: []; open: [port: string, baudRate: number]; close: []; resetCounters: [] }>()
const port = ref('')
const baudRate = 921_600

watch(() => [props.ports, props.status] as const, () => {
  port.value = props.status?.port ?? port.value ?? props.ports[0]?.port ?? ''
  if (!port.value && props.ports[0]) port.value = props.ports[0].port
}, { immediate: true, deep: true })
</script>

<template>
  <section class="card serial-panel">
    <div class="section-head">
      <div><div class="card-title">串口控制</div></div>
      <div class="serial-head-actions">
        <button type="button" class="btn secondary reset-counters-button" title="仅清零 PC 与聚合器传输计数，不操作积木设备" :disabled="loading || counterResetBusy || !status?.isOpen" @click.stop="emit('resetCounters')">{{ counterResetBusy ? '清零中…' : '清零传输计数' }}</button>
        <StatusBadge :tone="status?.isOpen ? 'green' : status?.errorMessage ? 'red' : 'gray'" :text="status?.isOpen ? '已打开' : '未打开'" />
      </div>
    </div>
    <div class="serial-form">
      <select v-model="port" aria-label="串口" :disabled="status?.isOpen || counterResetBusy"><option v-if="!ports.length" value="">未发现串口</option><option v-for="item in ports" :key="item.port" :value="item.port">{{ item.port }} · {{ item.description }}</option></select>
      <div class="serial-actions">
        <button class="btn secondary" :disabled="loading || counterResetBusy || status?.isOpen" @click="emit('refresh')">刷新</button>
        <button class="btn primary" :disabled="loading || counterResetBusy || status?.isOpen || !port" @click="emit('open', port, baudRate)">打开</button>
        <button class="btn danger" :disabled="loading || counterResetBusy || !status?.isOpen" @click="emit('close')">关闭</button>
      </div>
    </div>
    <div class="serial-metrics">
      <div><span>当前端口</span><b>{{ status?.port ?? '—' }}</b></div><div><span>业务帧</span><b>{{ status?.frameCount.toLocaleString() ?? '—' }}</b></div><div><span>网关丢帧</span><b>{{ status?.gatewayLostFrames ?? '—' }}</b></div><div><span>CRC / COBS</span><b>{{ status ? `${status.crcErrorCount} / ${status.cobsErrorCount}` : '—' }}</b></div><div><span>主机会话</span><b>{{ status?.hostSessionReady ? '已建立' : '未建立' }}</b></div>
    </div>
    <p v-if="status?.errorMessage" class="panel-error">{{ status.errorMessage }}</p>
  </section>
</template>

<style scoped>
.serial-panel{padding:16px}.serial-head-actions{display:flex;align-items:center;gap:8px}.reset-counters-button{padding:6px 10px;font-size:10px}.reset-counters-button:disabled{background:#eef2f6;color:#9aa5b5;cursor:not-allowed}.serial-form{display:grid;grid-template-columns:minmax(180px,1fr) auto;gap:10px;margin-top:13px;align-items:center}.serial-actions{display:flex;gap:7px}.serial-actions .btn{padding:9px 12px}.serial-metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin-top:12px}.serial-metrics>div{padding:9px 10px;border-radius:8px;background:#f8fafc}.serial-metrics span{display:block;color:#7b879a;font-size:9px}.serial-metrics b{display:block;margin-top:4px;color:#354259;font-size:12px}.panel-error{margin:10px 0 0;color:#b53f3f;font-size:11px}@media(max-width:900px){.serial-metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.serial-head-actions{align-items:flex-end;flex-direction:column}.serial-form,.serial-metrics{grid-template-columns:1fr}.serial-actions{grid-column:auto}}
</style>

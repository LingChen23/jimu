<script setup lang="ts">
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { DeviceSnapshot } from '@/types/device'
import {
  batteryLevelText,
  batteryLevelTone,
  batteryVoltageText,
  connectionText,
  connectionTone,
  currentTaskText,
  integrationText,
  motionText,
  packetCountText,
  packetLossText,
  streamingText,
  usageText,
  usageTone,
} from '../device.formatters'

const props = defineProps<{
  device: DeviceSnapshot
  loading: boolean
  serialOpen: boolean
  commandBusy: boolean
}>()
const emit = defineEmits<{ refresh: []; openRealtime: [device: DeviceSnapshot]; clearCounters: [] }>()
const tested = () => props.device.integrationStatus === 'tested'
</script>

<template>
  <aside class="card device-detail">
    <div class="device-detail-head">
      <div class="device-title">
        <div class="device-avatar">{{ device.deviceId }}</div>
        <div>
          <small>当前选择</small>
          <h2>{{ device.displayName }}</h2>
          <div class="badge-row">
            <StatusBadge :tone="tested() ? 'green' : 'gray'" :text="integrationText(device)" />
            <StatusBadge :tone="connectionTone(device)" :text="connectionText(device)" />
            <StatusBadge :tone="usageTone(device)" :text="usageText(device)" />
          </div>
        </div>
      </div>
      <div class="detail-actions">
        <button class="btn secondary" :disabled="!tested() || loading" @click="emit('refresh')">{{ loading ? '刷新中…' : '刷新详情' }}</button>
        <button class="btn primary" :disabled="!tested()" @click="emit('openRealtime', device)">进入实时监控</button>
      </div>
    </div>

    <section class="detail-section">
      <div class="detail-section-title">身份与当前状态</div>
      <div class="detail-grid">
        <div><span>积木编号</span><b>{{ device.blockId }}</b></div>
        <div><span>硬件设备ID</span><b>{{ device.deviceId }}</b></div>
        <div><span>当前任务</span><b>{{ currentTaskText(device) }}</b></div>
        <div><span>运动状态</span><b>{{ motionText(device) }}</b></div>
        <div><span>所属网关</span><b>{{ tested() ? device.gatewayName : '—' }}</b></div>
        <div><span>数据流</span><b>{{ streamingText(device) }}</b></div>
      </div>
    </section>

    <section class="detail-section">
      <div class="detail-section-title">设备健康</div>
      <div class="detail-grid">
        <div><span>电池</span><b>{{ batteryVoltageText(device) }}</b><StatusBadge class="battery-level" :tone="batteryLevelTone(device)" :text="batteryLevelText(device)" /></div>
        <div><span>加速度 / 角速度量程</span><b>{{ device.health.firmware ? `±${device.health.firmware.accelerationRangeG} g / ±${device.health.firmware.gyroscopeRangeDps} °/s` : '—' }}</b></div>
      </div>
    </section>

    <section class="detail-section">
      <div class="detail-section-head">
        <div class="detail-section-title">数据与错误计数</div>
        <button class="btn secondary clear-counters-button" :disabled="!tested() || !serialOpen || commandBusy" @click="emit('clearCounters')">
          {{ commandBusy ? '处理中…' : '清零' }}
        </button>
      </div>
      <div class="detail-grid">
        <div><span>接收数据包</span><b>{{ packetCountText(device) }}</b></div>
        <div><span>丢包数量</span><b>{{ packetLossText(device) }}</b></div>
        <div><span>采样缺口</span><b>{{ tested() ? device.health.errors.sampleGapCount : '—' }}</b></div>
        <div><span>传感器错误</span><b>{{ tested() ? device.health.errors.sensorErrorCount : '—' }}</b></div>
        <div><span>设备队列丢弃</span><b>{{ tested() ? device.health.errors.queueDropCount : '—' }}</b></div>
        <div><span>BLE 资源等待</span><b>{{ tested() ? device.health.errors.bleResourceWaitCount : '—' }}</b></div>
      </div>
    </section>

  </aside>
</template>

<style scoped>
.device-detail{padding:20px}.device-detail-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding-bottom:18px;border-bottom:1px solid #e4e9f1}.device-detail-head .device-title{min-width:0}.device-detail-head .device-title h2{margin:2px 0 7px}.detail-actions{display:flex;flex:0 0 auto;gap:8px}.detail-actions .btn{padding:10px 14px}.detail-actions .btn:disabled{background:#dce3ec;color:#8b96a5;cursor:not-allowed}.detail-section{padding-top:18px}.detail-section+.detail-section{margin-top:18px;border-top:1px solid #e4e9f1}.detail-section-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.detail-section-title{font-size:13px;font-weight:800}.clear-counters-button{padding:6px 12px;font-size:10px}.clear-counters-button:disabled{background:#eef2f6;color:#9aa5b5;cursor:not-allowed}.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:11px}.detail-grid>div{min-width:0;padding:12px;border:1px solid #edf1f6;border-radius:10px;background:#f8fafc}.detail-grid>div>span:not(.badge){display:block;color:#7b879a;font-size:10px}.detail-grid b{display:block;margin-top:6px;font-size:12px;line-height:1.45;overflow-wrap:anywhere}.detail-grid small{display:block;margin-top:4px;color:#8b97a8;font-size:9px;line-height:1.4}.detail-grid .battery-level{display:table;margin-top:5px}@media(max-width:1280px){.detail-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:860px){.detail-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:680px){.device-detail-head{flex-direction:column}.detail-actions{width:100%;flex-direction:column}.detail-actions .btn{width:100%}.detail-grid{grid-template-columns:1fr}}
</style>

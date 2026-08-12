<script setup lang="ts">
import type {
  DeviceConnectionFilter,
  DeviceIntegrationFilter,
  DeviceUsageFilter,
} from '../device.filters'

const keyword = defineModel<string>('keyword', { required: true })
const connection = defineModel<DeviceConnectionFilter>('connection', { required: true })
const usage = defineModel<DeviceUsageFilter>('usage', { required: true })
const integration = defineModel<DeviceIntegrationFilter>('integration', { required: true })
</script>

<template>
  <section class="card device-filters">
    <label>积木编号 / 硬件设备ID<input v-model="keyword" placeholder="例如：BLOCK-03、3 或网关A" /></label>
    <label>接入状态<select v-model="integration"><option value="all">全部</option><option value="tested">已实测</option><option value="pending-integration">待接入</option></select></label>
    <label>连接状态<select v-model="connection"><option value="all">全部</option><option value="online">在线</option><option value="offline">离线 / 未接入</option></select></label>
    <label>使用状态<select v-model="usage"><option value="all">全部</option><option value="idle">空闲</option><option value="collecting">采集中</option><option value="unavailable">不可用</option></select></label>
  </section>
</template>

<style scoped>
.device-filters{padding:16px 18px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}@media(max-width:1000px){.device-filters{grid-template-columns:repeat(2,1fr)}}@media(max-width:680px){.device-filters{grid-template-columns:1fr}}
</style>

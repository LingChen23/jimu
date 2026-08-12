<script setup lang="ts">
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { AggregatorStatus } from '@/types/serial'

defineProps<{ status: AggregatorStatus | null; serialOpen: boolean }>()
const gatewayName = (id: number) => String.fromCharCode(65 + id)
const compact = (value: number) => value.toLocaleString()
</script>

<template>
  <section class="card aggregator-panel">
    <div class="section-head">
      <div class="card-title">三路聚合器运行状态</div>
      <StatusBadge :tone="status?.usbReady ? 'green' : 'gray'" :text="status?.usbReady ? 'USB 就绪' : serialOpen ? '等待状态帧' : '串口未打开'" />
    </div>
    <div v-if="status" class="aggregator-grid">
      <article v-for="link in status.links" :key="link.gatewayId" class="gateway-card" :class="{ inactive: !link.active }">
        <div class="gateway-head"><strong>网关 {{ gatewayName(link.gatewayId) }}</strong><StatusBadge :tone="link.active ? 'green' : 'gray'" :text="link.active ? '活跃' : '等待'" /></div>
        <div class="gateway-metrics"><span>UART {{ link.uartPort }}</span><span>{{ compact(link.validFrames) }} 帧</span></div>
      </article>
    </div>
    <div v-else class="aggregator-empty">{{ serialOpen ? '正在等待聚合器健康状态帧。' : '打开聚合器串口后显示三路运行状态。' }}</div>
  </section>
</template>

<style scoped>
.aggregator-panel{padding:16px;display:flex;flex-direction:column}.aggregator-grid{display:grid;grid-template-columns:repeat(3,1fr);align-items:center;flex:1;gap:8px}.gateway-card{min-width:0;padding:11px;border:1px solid #e6ebf2;border-radius:10px;background:#f8fafc}.gateway-head{display:flex;align-items:center;justify-content:space-between;gap:6px}.gateway-metrics{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:9px}.gateway-metrics span{color:#59667a;font-size:10px}.gateway-card.inactive{opacity:.65}.aggregator-empty{min-height:88px;margin-top:13px;border:1px dashed #d5dfeb;border-radius:10px;display:grid;place-items:center;color:#7b879a;font-size:11px}@media(max-width:900px){.aggregator-grid{grid-template-columns:1fr}}
</style>

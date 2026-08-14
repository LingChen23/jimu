<script setup lang="ts">
import { init, use, type ECharts } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { AxisPoint } from '@/types/motion'

const props = defineProps<{ title: string; subtitle?: string; unit: string; data: AxisPoint[] }>()
const el = ref<HTMLDivElement | null>(null)

use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

let chart: ECharts | null = null
function time(ts: number) { return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false }) }
function render() {
  if (!el.value) return
  chart ??= init(el.value)
  chart.setOption({
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { data: ['X轴', 'Y轴', 'Z轴'], right: 6, top: 2 },
    grid: { left: 50, right: 20, top: 42, bottom: 30 },
    xAxis: { type: 'category', boundaryGap: false, data: props.data.map((p) => time(p.timestamp)), axisLabel: { interval: Math.max(1, Math.floor(props.data.length / 6)) } },
    yAxis: { type: 'value', name: props.unit, splitLine: { lineStyle: { color: '#edf1f7' } } },
    series: (['x', 'y', 'z'] as const).map((key, index) => ({
      name: ['X轴', 'Y轴', 'Z轴'][index],
      type: 'line',
      showSymbol: false,
      lineStyle: { width: 2 },
      data: props.data.map((p) => p[key]),
    })),
  })
}
function resize() { chart?.resize() }
onMounted(() => { render(); window.addEventListener('resize', resize) })
watch(() => props.data, render, { deep: true })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); chart?.dispose() })
</script>
<template>
  <section class="card chart-card">
    <div class="card-title">{{ title }}</div>
    <div v-if="subtitle" class="card-subtitle">{{ subtitle }}</div>
    <div ref="el" class="axis-chart"></div>
  </section>
</template>

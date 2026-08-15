<script setup lang="ts">
import { init, use, type ECharts } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { AxisPoint } from '@/types/motion'
import {
  relativeSeconds,
  relativeTimeAxisMaximum,
  relativeTimeLabel,
  relativeTimeTickInterval,
} from './axis-chart-time'

const props = defineProps<{ title: string; subtitle?: string; unit: string; data: AxisPoint[] }>()
const el = ref<HTMLDivElement | null>(null)

use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

let chart: ECharts | null = null

interface TooltipParameter {
  marker?: string
  seriesName?: string
  value?: number[]
}

function tooltipText(parameters: TooltipParameter | TooltipParameter[]): string {
  const rows = Array.isArray(parameters) ? parameters : [parameters]
  const elapsed = Number(rows[0]?.value?.[0] ?? 0)
  return [
    relativeTimeLabel(elapsed),
    ...rows.map((row) => {
      const value = Number(row.value?.[1])
      return `${row.marker ?? ''}${row.seriesName ?? ''}：${Number.isFinite(value) ? value.toFixed(3) : '—'} ${props.unit}`
    }),
  ].join('<br>')
}

function render() {
  if (!el.value) return
  chart ??= init(el.value)
  const elapsed = relativeSeconds(props.data)
  const duration = elapsed.at(-1) ?? 0
  chart.setOption({
    animation: false,
    tooltip: {
      trigger: 'axis',
      formatter: tooltipText,
      confine: true,
      axisPointer: {
        type: 'line',
        snap: true,
      },
    },
    legend: { data: ['X轴', 'Y轴', 'Z轴'], right: 6, top: 2 },
    grid: { left: 50, right: 20, top: 42, bottom: 42 },
    xAxis: {
      type: 'value',
      min: 0,
      max: relativeTimeAxisMaximum(duration),
      interval: relativeTimeTickInterval(duration),
      name: '相对时间',
      nameLocation: 'middle',
      nameGap: 28,
      axisPointer: { snap: true },
      axisLabel: { formatter: relativeTimeLabel, hideOverlap: true },
    },
    yAxis: { type: 'value', name: props.unit, splitLine: { lineStyle: { color: '#edf1f7' } } },
    series: (['x', 'y', 'z'] as const).map((key, index) => ({
      name: ['X轴', 'Y轴', 'Z轴'][index],
      type: 'line',
      showSymbol: false,
      lineStyle: { width: 2 },
      data: props.data.map((point, pointIndex) => [elapsed[pointIndex], point[key]]),
    })),
  }, { notMerge: true })
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

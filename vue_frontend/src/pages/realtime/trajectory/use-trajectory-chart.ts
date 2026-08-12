import * as echarts from 'echarts'
import 'echarts-gl'
import {
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
  type ComputedRef,
  type Ref,
} from 'vue'
import type {
  TrajectoryDisplayMode,
  TrajectoryProjectionView,
  TrajectoryVector,
  TrajectoryView,
} from '@/utils/trajectory'
import {
  buildProjectionOption,
  buildThreeDimensionalOption,
} from './trajectory-chart-options'

interface UseTrajectoryChartInput {
  points: ComputedRef<TrajectoryVector[]>
  displayMode: Ref<TrajectoryDisplayMode>
  selectedView: Ref<TrajectoryView>
  displayUnit: ComputedRef<string>
  hasTrajectory: ComputedRef<boolean>
}

function trajectorySignature(points: TrajectoryVector[]): string {
  let hash = 0x811c9dc5
  for (const point of points) {
    for (const coordinate of point) {
      hash ^= Math.round(coordinate * 1_000_000_000)
      hash = Math.imul(hash, 0x01000193)
    }
  }
  return `${points.length}:${hash >>> 0}`
}

function detectWebGL(): boolean {
  if (typeof document === 'undefined') return false
  try {
    const canvas = document.createElement('canvas')
    return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'))
  } catch {
    return false
  }
}

export function useTrajectoryChart(input: UseTrajectoryChartInput) {
  const chartElement = ref<HTMLDivElement | null>(null)
  const renderError = ref<string | null>(null)
  const webglAvailable = ref(detectWebGL())
  let chart: echarts.ECharts | null = null
  let resizeObserver: ResizeObserver | null = null
  let scheduledFrame: number | null = null
  let lastConfigurationKey: string | null = null
  let lastRenderSignature: string | null = null

  function disposeChart(): void {
    chart?.dispose()
    chart = null
    lastConfigurationKey = null
    lastRenderSignature = null
  }

  function render(): void {
    const element = chartElement.value
    if (
      !element
      || !input.hasTrajectory.value
      || (input.selectedView.value === 'iso' && !webglAvailable.value)
    ) {
      disposeChart()
      return
    }

    try {
      renderError.value = null
      const isNewChart = chart === null
      chart ??= echarts.init(element)
      const configurationKey = `${input.selectedView.value}:${input.displayMode.value}`
      const configurationChanged = lastConfigurationKey !== configurationKey
      const sizeKey = input.selectedView.value === 'iso'
        ? ''
        : `:${element.clientWidth}x${element.clientHeight}`
      const renderSignature = `${configurationKey}:${trajectorySignature(input.points.value)}${sizeKey}`
      if (!isNewChart && !configurationChanged && lastRenderSignature === renderSignature) return
      const common = {
        points: input.points.value,
        unit: input.displayUnit.value,
        displayMode: input.displayMode.value,
      }
      const option = input.selectedView.value === 'iso'
        ? buildThreeDimensionalOption(common)
        : buildProjectionOption({
            ...common,
            view: input.selectedView.value as TrajectoryProjectionView,
            chartWidth: element.clientWidth,
            chartHeight: element.clientHeight,
          })
      chart.setOption(option as unknown as echarts.EChartsOption, {
        // Point updates retain the existing canvas, WebGL scene and series.
        // A full option replacement is only needed when the chart type or
        // coordinate representation changes.
        notMerge: isNewChart || configurationChanged,
        lazyUpdate: !configurationChanged,
      })
      lastConfigurationKey = configurationKey
      lastRenderSignature = renderSignature
    } catch (error) {
      disposeChart()
      renderError.value = error instanceof Error ? error.message : String(error)
    }
  }

  function scheduleRender(): void {
    void nextTick(() => {
      if (scheduledFrame !== null) return
      scheduledFrame = window.requestAnimationFrame(() => {
        scheduledFrame = null
        render()
      })
    })
  }

  watch(
    () => [
      input.points.value,
      input.displayMode.value,
      input.selectedView.value,
    ],
    scheduleRender,
    { deep: true },
  )
  watch(chartElement, (element, previous) => {
    if (previous) resizeObserver?.unobserve(previous)
    if (element) {
      resizeObserver?.observe(element)
      scheduleRender()
    }
  })

  onMounted(() => {
    resizeObserver = new ResizeObserver(() => {
      chart?.resize()
      if (input.selectedView.value !== 'iso') scheduleRender()
    })
    if (chartElement.value) resizeObserver.observe(chartElement.value)
    scheduleRender()
  })

  onBeforeUnmount(() => {
    if (scheduledFrame !== null) window.cancelAnimationFrame(scheduledFrame)
    scheduledFrame = null
    resizeObserver?.disconnect()
    disposeChart()
  })

  return { chartElement, renderError, webglAvailable }
}

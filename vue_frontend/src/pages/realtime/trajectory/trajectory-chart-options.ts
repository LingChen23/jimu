import {
  projectTrajectory,
  trajectoryProjection,
  trajectoryProjectionBounds,
  type TrajectoryPoint2D,
  type TrajectoryProjectionView,
  type TrajectoryVector,
} from '@/utils/trajectory'

export interface TrajectoryMarker {
  point: TrajectoryVector
  title: string
  description: string
  color: string
  speedMps?: number
}

export interface TrajectoryChartOption {
  animation: boolean
  backgroundColor: string
  series: unknown[]
  [key: string]: unknown
}

interface ThreeDimensionalOptionInput {
  points: TrajectoryVector[]
  unit: string
  speeds?: number[]
  markers?: TrajectoryMarker[]
}

interface ProjectionOptionInput extends ThreeDimensionalOptionInput {
  view: TrajectoryProjectionView
  chartWidth: number
  chartHeight: number
}

const projectionGrid = { left: 72, right: 30, top: 52, bottom: 62 }

function endpointSeries3D(points: TrajectoryVector[], name: string, color: string) {
  if (!points.length) return null
  const values = points.length === 1 ? [points[0]] : [points[0], points.at(-1)!]
  return {
    id: 'pc-trajectory-endpoints',
    name: `${name}端点`,
    type: 'scatter3D',
    coordinateSystem: 'cartesian3D',
    data: values,
    symbolSize: (_value: unknown, params: { dataIndex: number }) => params.dataIndex === values.length - 1 ? 9 : 6,
    itemStyle: { color, opacity: 1 },
    label: {
      show: true,
      formatter: (params: { dataIndex: number }) => values.length === 1
        ? '当前'
        : params.dataIndex === 0 ? '起点' : '当前',
      color,
      fontSize: 10,
      distance: 4,
    },
    emphasis: { disabled: true },
  }
}

function lineSeries3D(points: TrajectoryVector[], name: string, color: string, speeds: number[] = []) {
  if (!points.length) return null
  return {
    id: 'pc-trajectory-line',
    name,
    type: 'line3D',
    coordinateSystem: 'cartesian3D',
    data: points.map((point, index) => speeds.length ? [...point, speeds[index] ?? 0] : point),
    lineStyle: { color, width: 5, opacity: 0.96 },
  }
}

function markerSeries3D(markers: TrajectoryMarker[]) {
  if (!markers.length) return null
  return {
    id: 'pc-trajectory-analysis-markers', name: '轨迹事件', type: 'scatter3D', coordinateSystem: 'cartesian3D',
    data: markers.map((marker) => ({ name: marker.title, value: [...marker.point, marker.speedMps ?? 0], description: marker.description, itemStyle: { color: marker.color } })),
    symbolSize: 11,
    label: { show: true, formatter: '{b}', color: '#24324a', fontSize: 10, distance: 5 },
    emphasis: { disabled: true },
  }
}

function endpointSeries2D(
  points: TrajectoryPoint2D[],
  name: string,
  color: string,
  positions: { start: 'top' | 'bottom'; current: 'left' | 'right' },
) {
  if (!points.length) return null
  const data = points.length === 1
    ? [{ name: '当前', value: points[0], label: { position: positions.current } }]
    : [
        { name: '起点', value: points[0], label: { position: positions.start } },
        { name: '当前', value: points.at(-1)!, label: { position: positions.current } },
      ]
  return {
    id: 'pc-trajectory-endpoints',
    name: `${name}端点`,
    type: 'scatter',
    coordinateSystem: 'cartesian2d',
    data,
    symbolSize: (_value: unknown, params: { dataIndex: number }) => params.dataIndex === data.length - 1 ? 10 : 7,
    itemStyle: { color, opacity: 1 },
    label: {
      show: true,
      formatter: '{b}',
      color,
      fontSize: 10,
      distance: 5,
    },
    labelLayout: { hideOverlap: true },
    emphasis: { disabled: true },
    z: 4,
  }
}

function lineSeries2D(points: TrajectoryPoint2D[], name: string, color: string, speeds: number[] = []) {
  if (!points.length) return null
  return {
    id: 'pc-trajectory-line',
    name,
    type: 'line',
    coordinateSystem: 'cartesian2d',
    data: points.map((point, index) => speeds.length ? [...point, speeds[index] ?? 0] : point),
    showSymbol: false,
    lineStyle: { color, width: 3, opacity: 0.96 },
    itemStyle: { color },
    emphasis: { disabled: true },
    z: 3,
  }
}

function markerSeries2D(markers: TrajectoryMarker[], view: TrajectoryProjectionView) {
  if (!markers.length) return null
  const projection = trajectoryProjection(view)
  return {
    id: 'pc-trajectory-analysis-markers', name: '轨迹事件', type: 'scatter', coordinateSystem: 'cartesian2d',
    data: markers.map((marker) => ({
      name: marker.title,
      value: [marker.point[projection.horizontalIndex], marker.point[projection.verticalIndex], marker.speedMps ?? 0],
      description: marker.description,
      itemStyle: { color: marker.color },
    })),
    symbolSize: 11,
    label: { show: true, formatter: '{b}', position: 'top', color: '#24324a', fontSize: 10 },
    labelLayout: { hideOverlap: true }, emphasis: { disabled: true }, z: 6,
  }
}

function speedVisualMap(speeds: number[], dimension: number) {
  const finite = speeds.filter(Number.isFinite)
  if (!finite.length) return undefined
  const minimum = Math.min(...finite)
  const maximum = Math.max(...finite)
  return {
    show: true, type: 'continuous', min: minimum, max: maximum > minimum ? maximum : minimum + 1e-6,
    dimension, seriesIndex: [0], orient: 'horizontal', right: 18, bottom: 8,
    itemWidth: 110, itemHeight: 8, text: ['快', '慢'], textGap: 4, calculable: false,
    inRange: { color: ['#2563eb', '#14b8a6', '#f59e0b', '#dc2626'] },
  }
}

function formatAxisValue(value: number): string {
  if (!Number.isFinite(value) || value === 0) return '0'
  const magnitude = Math.abs(value)
  if (magnitude >= 1000 || magnitude < 0.001) return value.toExponential(1)
  const digits = magnitude < 1 ? 3 : magnitude < 100 ? 2 : 1
  return value.toFixed(digits).replace(/\.?0+$/, '')
}

function axis3D(name: string, unit: string) {
  return {
    type: 'value',
    name: `${name}（${unit}）`,
    nameTextStyle: { color: '#59667a', fontSize: 11 },
    axisLine: { lineStyle: { color: '#9aabc0' } },
    axisLabel: { color: '#738096', fontSize: 10 },
    splitLine: { lineStyle: { color: '#dfe7f1' } },
    splitArea: { show: false },
  }
}

function axis2D(name: string, unit: string, minimum: number, maximum: number, vertical: boolean) {
  return {
    type: 'value',
    name: `${name}（${unit}）`,
    nameLocation: 'middle',
    nameGap: vertical ? 52 : 38,
    nameRotate: vertical ? 90 : 0,
    min: minimum,
    max: maximum,
    scale: true,
    splitNumber: 5,
    nameTextStyle: { color: '#59667a', fontSize: 11, fontWeight: 600 },
    axisLine: { show: true, lineStyle: { color: '#9aabc0' } },
    axisTick: { show: true, length: 4, lineStyle: { color: '#9aabc0' } },
    axisLabel: {
      color: '#738096',
      fontSize: 10,
      margin: 10,
      hideOverlap: true,
      showMinLabel: false,
      showMaxLabel: false,
      formatter: formatAxisValue,
    },
    splitLine: { show: true, lineStyle: { color: '#e5ebf3', type: 'dashed' } },
  }
}

export function buildThreeDimensionalOption(input: ThreeDimensionalOptionInput): TrajectoryChartOption {
  const { points, unit, speeds = [], markers = [] } = input
  const series = [
    lineSeries3D(points, 'PC 惯导轨迹', '#1769e8', speeds),
    endpointSeries3D(points, 'PC 惯导轨迹', '#1769e8'),
    markerSeries3D(markers),
  ].filter((item) => item !== null)

  return {
    animation: false,
    backgroundColor: '#ffffff',
    visualMap: speedVisualMap(speeds, 3),
    tooltip: {
      show: true,
      confine: true,
      formatter: (params: { seriesName?: string; name?: string; value?: number[]; data?: { description?: string } }) => {
        const value = Array.isArray(params.value) ? params.value : []
        if (params.data?.description) return `${params.name ?? '轨迹事件'}<br>${params.data.description}`
        return [
          params.seriesName ?? '轨迹点',
          `X ${Number(value[0] ?? 0).toFixed(4)}`,
          `Y ${Number(value[1] ?? 0).toFixed(4)}`,
          `Z ${Number(value[2] ?? 0).toFixed(4)}`,
          ...(speeds.length ? [`速度 ${Number(value[3] ?? 0).toFixed(4)} m/s`] : []),
        ].join('<br>')
      },
    },
    xAxis3D: axis3D('X', unit),
    yAxis3D: axis3D('Y', unit),
    zAxis3D: axis3D('Z', unit),
    grid3D: {
      boxWidth: 100,
      boxHeight: 100,
      boxDepth: 100,
      top: 28,
      bottom: 10,
      environment: '#ffffff',
      axisPointer: { show: true },
      viewControl: {
        projection: 'orthographic',
        orthographicSize: 150,
        minOrthographicSize: 30,
        maxOrthographicSize: 360,
        alpha: 20,
        beta: 40,
        center: [0, 0, 0],
        autoRotate: false,
        rotateSensitivity: 1,
        zoomSensitivity: 1,
        panSensitivity: 1,
        damping: 0.8,
      },
      light: {
        main: { intensity: 0.7, shadow: false },
        ambient: { intensity: 0.75 },
      },
    },
    series,
  }
}

export function buildProjectionOption(input: ProjectionOptionInput): TrajectoryChartOption {
  const { points, view, unit, chartWidth, chartHeight, speeds = [], markers = [] } = input
  const projection = trajectoryProjection(view)
  const projectedPoints = projectTrajectory(points, view)
  const plotWidth = Math.max(chartWidth - projectionGrid.left - projectionGrid.right, 1)
  const plotHeight = Math.max(chartHeight - projectionGrid.top - projectionGrid.bottom, 1)
  const bounds = trajectoryProjectionBounds(
    [points],
    view,
    plotWidth / plotHeight,
    1,
  )
  const series = [
    lineSeries2D(projectedPoints, 'PC 惯导轨迹', '#1769e8', speeds),
    endpointSeries2D(projectedPoints, 'PC 惯导轨迹', '#1769e8', { start: 'top', current: 'right' }),
    markerSeries2D(markers, view),
  ].filter((item) => item !== null)

  return {
    animation: false,
    backgroundColor: '#ffffff',
    visualMap: speedVisualMap(speeds, 2),
    tooltip: {
      show: true,
      trigger: 'item',
      confine: true,
      formatter: (params: { seriesName?: string; name?: string; value?: number[]; data?: { description?: string } }) => {
        const value = Array.isArray(params.value) ? params.value : []
        if (params.data?.description) return `${params.name ?? '轨迹事件'}<br>${params.data.description}`
        return [
          (params.seriesName ?? '轨迹点').replace(/端点$/, ''),
          `${projection.horizontalAxis} ${Number(value[0] ?? 0).toFixed(4)} ${unit}`,
          `${projection.verticalAxis} ${Number(value[1] ?? 0).toFixed(4)} ${unit}`,
          ...(speeds.length ? [`速度 ${Number(value[2] ?? 0).toFixed(4)} m/s`] : []),
        ].join('<br>')
      },
    },
    grid: { ...projectionGrid, containLabel: false },
    xAxis: axis2D(
      projection.horizontalAxis,
      unit,
      bounds.minimumHorizontal,
      bounds.maximumHorizontal,
      false,
    ),
    yAxis: axis2D(
      projection.verticalAxis,
      unit,
      bounds.minimumVertical,
      bounds.maximumVertical,
      true,
    ),
    series,
  }
}

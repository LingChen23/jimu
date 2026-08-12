import { describe, expect, it } from 'vitest'
import type { TrajectoryVector } from '@/utils/trajectory'
import {
  buildProjectionOption,
  buildThreeDimensionalOption,
} from './trajectory-chart-options'

const pcPoints: TrajectoryVector[] = [[0, 0, 0], [1, 3, 0.25]]

describe('trajectory chart options', () => {
  it('builds the PC 3D path and endpoint series', () => {
    const option = buildThreeDimensionalOption({
      points: pcPoints,
      unit: '无量纲',
      displayMode: 'shape',
    })

    expect(option.series).toHaveLength(2)
    expect((option.grid3D as { boxWidth: number }).boxWidth).toBe(100)
  })

  it('uses metric dimensions and metric tooltip suffix', () => {
    const option = buildThreeDimensionalOption({
      points: pcPoints,
      unit: 'm',
      displayMode: 'metric',
    })
    const tooltip = option.tooltip as { formatter: (params: { value: number[] }) => string }

    expect((option.grid3D as { boxHeight: number }).boxHeight).toBeGreaterThan(0)
    expect(tooltip.formatter({ value: [1, 2, 3] })).toContain('1.0000 m')
  })

  it('builds an aspect-aware XY projection', () => {
    const option = buildProjectionOption({
      points: pcPoints,
      unit: 'm',
      displayMode: 'metric',
      view: 'xy',
      chartWidth: 900,
      chartHeight: 500,
    })
    const xAxis = option.xAxis as { name: string; min: number; max: number }
    const yAxis = option.yAxis as { name: string; min: number; max: number }

    expect(option.series).toHaveLength(2)
    expect(xAxis.name).toBe('X（m）')
    expect(yAxis.name).toBe('Y（m）')
    expect(xAxis.max - xAxis.min).toBeGreaterThan(yAxis.max - yAxis.min)
  })

  it('returns an empty series list for missing trajectories', () => {
    const option = buildProjectionOption({
      points: [],
      unit: '无量纲',
      displayMode: 'shape',
      view: 'yz',
      chartWidth: 600,
      chartHeight: 400,
    })

    expect(option.series).toEqual([])
  })
})

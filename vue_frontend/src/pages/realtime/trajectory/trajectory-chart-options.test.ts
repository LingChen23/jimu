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
    })

    expect(option.series).toHaveLength(2)
    expect((option.grid3D as { boxWidth: number }).boxWidth).toBe(100)
  })

  it('uses a fixed relative-shape cube', () => {
    const option = buildThreeDimensionalOption({
      points: pcPoints,
      unit: '无量纲',
    })
    const tooltip = option.tooltip as { formatter: (params: { value: number[] }) => string }

    expect((option.grid3D as { boxHeight: number }).boxHeight).toBe(100)
    expect(tooltip.formatter({ value: [1, 2, 3] })).toContain('1.0000')
  })

  it('builds an aspect-aware XY projection', () => {
    const option = buildProjectionOption({
      points: pcPoints,
      unit: '无量纲',
      view: 'xy',
      chartWidth: 900,
      chartHeight: 500,
    })
    const xAxis = option.xAxis as { name: string; min: number; max: number }
    const yAxis = option.yAxis as { name: string; min: number; max: number }

    expect(option.series).toHaveLength(2)
    expect(xAxis.name).toBe('X（无量纲）')
    expect(yAxis.name).toBe('Y（无量纲）')
    expect(xAxis.max - xAxis.min).toBeGreaterThan(yAxis.max - yAxis.min)
  })

  it('adds speed coloring and analysis markers for result trajectories', () => {
    const option = buildProjectionOption({
      points: pcPoints,
      speeds: [0.1, 1.2],
      markers: [{ point: pcPoints[1], title: '掉落', description: '掉落冲击 3.20 g', color: '#dc2626' }],
      unit: '无量纲',
      view: 'xy',
      chartWidth: 900,
      chartHeight: 500,
    })

    expect(option.series).toHaveLength(3)
    expect(option.visualMap).toBeTruthy()
  })

  it('returns an empty series list for missing trajectories', () => {
    const option = buildProjectionOption({
      points: [],
      unit: '无量纲',
      view: 'yz',
      chartWidth: 600,
      chartHeight: 400,
    })

    expect(option.series).toEqual([])
  })
})

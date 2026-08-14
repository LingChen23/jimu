import { describe, expect, it } from 'vitest'
import { isPresentationRoute, isWorkspaceRoute } from './app-layout-route'

describe('app layout route scale', () => {
  it('keeps history and its detail routes in the same workspace scale', () => {
    expect(isWorkspaceRoute('/history')).toBe(true)
    expect(isWorkspaceRoute('/results/T20260811-175414-6BBD')).toBe(true)
    expect(isWorkspaceRoute('/results/movements/1/pc/2')).toBe(true)
  })

  it('keeps realtime on the presentation class', () => {
    expect(isPresentationRoute('/realtime')).toBe(true)
    expect(isWorkspaceRoute('/realtime')).toBe(false)
  })
})

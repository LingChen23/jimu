import { describe, expect, it } from 'vitest'
import { parseResultRoute } from './result-route'

describe('result route parser', () => {
  it('parses task and movement routes', () => {
    expect(parseResultRoute('task-result', { taskId: 'T-1' })).toEqual({
      kind: 'task',
      taskId: 'T-1',
    })
    expect(parseResultRoute('movement-result', {
      deviceId: '3',
      movementId: '2',
      analysisMode: 'pc',
    })).toEqual({
      kind: 'movement',
      deviceId: 3,
      movementId: 2,
      analysisMode: 'pc',
    })
  })

  it('rejects ambiguous route parameters', () => {
    expect(() => parseResultRoute('movement-result', {
      deviceId: '0',
      movementId: '2',
      analysisMode: 'unknown',
    })).toThrow()
  })
})

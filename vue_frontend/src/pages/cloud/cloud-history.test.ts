import { describe, expect, it } from 'vitest'
import { formatBytes, historyQuery } from './cloud-history'

describe('cloud history helpers', () => {
  it('builds a trimmed server-side filter query', () => {
    const query = new URLSearchParams(historyQuery({
      taskId: ' T-1 ', participantId: '', participantName: '张 三', date: '2026-09-21',
    }, 2))
    expect(Object.fromEntries(query)).toEqual({
      page: '2', page_size: '20', task_id: 'T-1', participant_name: '张 三', date: '2026-09-21',
    })
  })

  it('formats archive sizes for the table', () => {
    expect(formatBytes(783)).toBe('783 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(2 * 1024 * 1024)).toBe('2.0 MB')
  })
})

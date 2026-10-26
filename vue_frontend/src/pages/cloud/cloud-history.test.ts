import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchWithTimeout } from '@/services/http-client'
import { deleteArchivedTasks, formatBytes, historyQuery } from './cloud-history'

vi.mock('@/services/http-client', () => ({ fetchWithTimeout: vi.fn() }))
beforeEach(() => vi.resetAllMocks())

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

  it('deletes selected tasks directly and continues after a per-task failure', async () => {
    vi.mocked(fetchWithTimeout)
      .mockResolvedValueOnce(Response.json({ task_id: 'T-1', deleted: true }))
      .mockResolvedValueOnce(Response.json({ error: { code: 'oss_delete_failed' } }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ task_id: 'T-3', deleted: true }))
    const onDeleted = vi.fn()
    const result = await deleteArchivedTasks(['T-1', 'T-2', 'T-3', 'T-1'], onDeleted)
    expect(result.deleted).toEqual(['T-1', 'T-3'])
    expect(result.failures).toEqual([{ taskId: 'T-2', message: '文件删除失败，请重试' }])
    expect(onDeleted.mock.calls).toEqual([['T-1'], ['T-3']])
    expect(fetchWithTimeout).toHaveBeenCalledWith('/api/history/tasks/T-1', {
      method: 'DELETE',
    }, 130_000)
  })

  it('preserves a task when deletion is not confirmed', async () => {
    const onDeleted = vi.fn()
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(Response.json({ deleted: false, task_id: 'T-1' }))
    const result = await deleteArchivedTasks(['T-1'], onDeleted)
    expect(result.failures[0]?.message).toBe('未确认删除成功，请重试')
    expect(onDeleted).not.toHaveBeenCalled()
  })
})

import type { AnalysisMode } from '@/types/motion'

export type ResultRouteReference =
  | { kind: 'task'; taskId: string }
  | { kind: 'movement'; deviceId: number; movementId: number; analysisMode: AnalysisMode }

function routeParam(value: unknown): string {
  return Array.isArray(value) ? String(value[0] ?? '') : String(value ?? '')
}

function positiveInteger(value: unknown, label: string): number {
  const parsed = Number(routeParam(value))
  if (!Number.isInteger(parsed) || parsed <= 0) throw new Error(`${label}无效`)
  return parsed
}

function analysisMode(value: unknown): AnalysisMode {
  const parsed = routeParam(value)
  if (parsed !== 'pc' && parsed !== 'device') throw new Error('分析模式无效')
  return parsed
}

export function parseResultRoute(
  name: unknown,
  params: Record<string, unknown>,
): ResultRouteReference {
  if (name === 'task-result') {
    const taskId = routeParam(params.taskId)
    if (!taskId) throw new Error('任务编号无效')
    return { kind: 'task', taskId }
  }
  if (name === 'movement-result') {
    return {
      kind: 'movement',
      deviceId: positiveInteger(params.deviceId, '硬件设备ID'),
      movementId: positiveInteger(params.movementId, '移动编号'),
      analysisMode: analysisMode(params.analysisMode),
    }
  }
  throw new Error('结果详情路由无效')
}

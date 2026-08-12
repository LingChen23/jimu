import { MockMotionDataService } from './mock-motion-data.service'
import { MotionSystemDataService } from './motion-system-data.service'
import type { MotionDataService } from './motion-data.service'

type DataSource = 'mock' | 'motion-system'

const providers: Record<DataSource, () => MotionDataService> = {
  mock: () => new MockMotionDataService(),
  'motion-system': () => new MotionSystemDataService(),
}

export function createMotionDataService(source: string): MotionDataService {
  const provider = providers[source as DataSource]
  if (!provider) throw new Error(`不支持的数据源: ${source}`)
  return provider()
}

const configuredDataSource = import.meta.env.VITE_DATA_SOURCE ?? 'motion-system'

export const motionDataService: MotionDataService = createMotionDataService(configuredDataSource)
export type { MotionDataService } from './motion-data.service'

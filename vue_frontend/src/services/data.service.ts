import { MotionSystemDataService } from './motion-system-data.service'
import type { MotionDataService } from './motion-data.service'

export const motionDataService: MotionDataService = new MotionSystemDataService()
export type { MotionDataService } from './motion-data.service'

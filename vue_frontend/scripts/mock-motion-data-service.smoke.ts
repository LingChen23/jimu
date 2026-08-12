import assert from 'node:assert/strict'
import { MockMotionDataService } from '../src/services/mock-motion-data.service'
import { prepareTrajectory } from '../src/utils/trajectory'

async function run(): Promise<void> {
  const service = new MockMotionDataService()
  const devices = await service.listDevices()

  assert.equal(devices.length, 12)
  assert.equal(devices.filter((device) => device.integrationStatus === 'tested').length, 4)
  assert.equal(devices.filter((device) => device.integrationStatus === 'pending-integration').length, 8)

  const testedSnapshot = await service.getMotionSnapshot({ deviceId: 1 })
  assert.equal(testedSnapshot.trajectories.device.source, 'device')
  assert.equal(testedSnapshot.trajectories.device.coordinateSystem, 'device-relative')
  assert.equal(testedSnapshot.trajectories.device.unit, 'm')
  assert.equal(testedSnapshot.trajectories.pcInertial.source, 'pc-inertial')
  assert.equal(testedSnapshot.trajectories.pcInertial.coordinateSystem, 'world-relative')
  assert.equal(testedSnapshot.trajectories.pcInertial.unit, 'm')
  assert.ok(testedSnapshot.trajectories.device.points.length > 0)
  assert.ok(testedSnapshot.trajectories.pcInertial.points.length > 0)
  assert.deepEqual(
    testedSnapshot.trajectories.device.points[0],
    { timestamp: testedSnapshot.trajectories.device.points[0].timestamp, x: 0, y: 0, z: 0 },
  )

  const relativeDevice = prepareTrajectory(testedSnapshot.trajectories.device.points, 'shape')
  const metricDevice = prepareTrajectory(testedSnapshot.trajectories.device.points, 'metric')
  assert.deepEqual(relativeDevice.points[0], [0, 0, 0])
  assert.deepEqual(metricDevice.points.at(-1), [
    testedSnapshot.trajectories.device.points.at(-1)?.x,
    testedSnapshot.trajectories.device.points.at(-1)?.y,
    testedSnapshot.trajectories.device.points.at(-1)?.z,
  ])
  const relativeAxisExtent = [0, 1, 2].map((axis) => {
    const values = relativeDevice.points.map((point) => point[axis])
    return Math.max(...values) - Math.min(...values)
  })
  const relativeRadialExtent = Math.max(...relativeDevice.points.map((point) => Math.hypot(...point)))
  assert.ok(Math.abs(Math.max(...relativeAxisExtent, relativeRadialExtent) - 1) < 1e-9)

  const pendingSnapshot = await service.getMotionSnapshot({ deviceId: 5 })
  assert.equal(pendingSnapshot.available, false)
  assert.equal(pendingSnapshot.trajectories.device.points.length, 0)
  assert.equal(pendingSnapshot.trajectories.pcInertial.points.length, 0)

  const initialStatus = await service.getSerialStatus()
  assert.equal(initialStatus.isOpen, false)
  assert.equal(initialStatus.receiveStatus, 'idle')

  const [port] = await service.listSerialPorts()
  assert.ok(port)
  const openStatus = await service.openSerial({ port: port.port, baudRate: 921_600 })
  assert.equal(openStatus.isOpen, true)
  assert.equal(openStatus.receiveStatus, 'receiving')

  const testedResult = await service.executeDeviceCommand({ deviceId: 1, command: 'GET_INFO' })
  assert.equal(testedResult.ok, true)

  const pendingResult = await service.executeDeviceCommand({ deviceId: 5, command: 'GET_INFO' })
  assert.equal(pendingResult.ok, false)
  assert.equal(pendingResult.failureCode, 'pending-integration')

  await assert.rejects(
    () => service.getMotionSnapshot({ deviceId: 99 }),
    /未知设备ID/,
  )

  const records = await service.listMovements({ deviceId: 1, analysisMode: 'pc' })
  assert.ok(records.length > 0)
  const request = { deviceId: 1, movementId: records[0].movementId, analysisMode: 'pc' as const }
  const detail = await service.getMovementDetail(request)
  assert.equal(detail.gyroscope.length, detail.acceleration.length)
  assert.equal(detail.orientation.initialized, true)
  assert.ok(detail.events.length > 0)
  const csv = await service.exportMovement({ ...request, format: 'csv' })
  const json = await service.exportMovement({ ...request, format: 'json' })
  assert.ok(csv.content.trim().length > 0)
  assert.ok(json.content.trim().length > 0)

  await assert.rejects(
    () => service.getTaskResult({ taskId: 'missing-task' }),
    /未找到任务记录/,
  )

  const closedStatus = await service.closeSerial()
  assert.equal(closedStatus.isOpen, false)
  assert.equal(closedStatus.receiveStatus, 'idle')

  console.log('MockMotionDataService smoke passed: dual trajectories, 4 tested, 8 pending, serial, commands, errors, CSV/JSON')
}

await run()

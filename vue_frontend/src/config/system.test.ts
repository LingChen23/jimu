import { describe, expect, it } from 'vitest'
import { gatewayNameForDevice } from './system'

describe('gateway device routing', () => {
  it('uses the same A/B/C grouping as the gateway and aggregator firmware', () => {
    expect([1, 2, 3, 4].map(gatewayNameForDevice)).toEqual(Array(4).fill('网关A'))
    expect([5, 6, 7].map(gatewayNameForDevice)).toEqual(Array(3).fill('网关B'))
    expect([8, 9, 10].map(gatewayNameForDevice)).toEqual(Array(3).fill('网关C'))
  })
})

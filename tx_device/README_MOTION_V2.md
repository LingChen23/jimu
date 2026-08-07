# Motion V2 TX

Motion V2 is the 100-byte air-interface format used by nRF firmware 0.2.x.
It reduces BLE notification load without reducing the 100 Hz IMU sample rate:

- Motion V1: 4 samples/packet, 25 notifications/s per device;
- Motion V2: 6 samples/packet, about 16.67 notifications/s per device;
- 12-device total: about 300 notifications/s becomes about 200 notifications/s.

The packet remains exactly 100 bytes and still carries six-axis raw IMU data,
the latest device coordinate, quality flags, session identity and sequence
numbers. Gateways and the PC decoder accept V1 and V2 concurrently so devices
can be upgraded one at a time.

## Sensor data

```text
sample rate:        100 Hz
acceleration:       +/-8 g, 4096 LSB/g
angular rate:       +/-1000 deg/s, 32.8 LSB/(deg/s)
sample axis order:  ax, ay, az, gx, gy, gz
```

## Packet layout

All multibyte fields are little-endian.

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | protocol version = 2 |
| 1 | 1 | packet type = 0x10 |
| 2 | 1 | device ID |
| 3 | 1 | packet flags |
| 4 | 4 | session ID |
| 8 | 4 | first sample sequence |
| 12 | 4 | low 32 bits of first sample RTC tick at 32768 Hz |
| 16 | 2 | packet sequence |
| 18 | 1 | sample count = 6 |
| 19 | 1 | IMU format = signed raw int16 |
| 20 | 72 | six samples; each is six signed int16 axes |
| 92 | 6 | x/y/z in signed int16 millimetres |
| 98 | 1 | coordinate age in 100 Hz samples |
| 99 | 1 | coordinate flags |

The coordinate source sequence is reconstructed as:

```text
first_sample_sequence + 5 - coordinate_age
```

Coordinate range is `-32768..32767 mm`, which is larger than the intended
building-block workspace. Values outside that range are saturated. All eight
currently defined coordinate flags fit in the one-byte field.

V2 stores one real RTC anchor per six-sample packet. The PC reconstructs the
five internal sample times at the fixed 100 Hz cadence and uses the next real
packet anchor to prevent long-term timing drift. The 32-bit RTC value wraps
after about 36.4 hours; existing modulo-32-bit time-difference handling makes
the wrap continuous.

## Upgrade and acceptance

Open `tx.uvprojx`, select target `nrf52832_xxaa`, and set the correct
`TX_DEVICE_ID` before each rebuild and download. After each device reconnects:

1. `GET_INFO` must show firmware `0.2.1`, protocol `2`, sample rate `100 Hz`;
2. the PC data source must show `完整 100B（V2/6样本）`;
3. after all 12 devices are upgraded, clear device and PC packet counters;
4. run a 60-second all-device test and require zero sequence gaps, zero nRF
   queue drops, zero gateway/aggregator drops and no disconnects.

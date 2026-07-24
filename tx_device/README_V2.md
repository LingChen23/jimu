# TX Project V2

This is the writable realtime TX project. The read-only `tx_project` baseline
and the validated `tx_ble_test` project are unchanged.

## Runtime

- ICM-20689 data-ready sampling: 100 Hz
- Raw IMU notification generation: 100 Hz
- BLE NUS notification payload: fixed 20-byte binary frame
- Preferred BLE connection interval: 15 ms
- Device ID: `1` (`TRAJECTORY_DEVICE_ID` in `src/main.c`)
- No active 20 ms blocking send delay

The TX state estimator still calibrates independently and emits movement state
and ORIGIN/STOP events. Position is no longer transmitted. The computer uses
the raw six-axis samples to calculate and post-correct trajectory segments.

## Frame

| Offset | Size | Field | Encoding |
| --- | ---: | --- | --- |
| 0 | 1 | start | `0xA5` |
| 1 | 1 | device_id | `1..255` |
| 2 | 2 | sequence | uint16, little-endian |
| 4 | 2 | accel_x | int16 raw sensor count, little-endian |
| 6 | 2 | accel_y | int16 raw sensor count, little-endian |
| 8 | 2 | accel_z | int16 raw sensor count, little-endian |
| 10 | 2 | gyro_x | int16 raw sensor count, little-endian |
| 12 | 2 | gyro_y | int16 raw sensor count, little-endian |
| 14 | 2 | gyro_z | int16 raw sensor count, little-endian |
| 16 | 2 | sample_tick_10ms | uint16, little-endian |
| 18 | 1 | flags | state/event bits |
| 19 | 1 | end | `0x5A` |

Flags: `0x01` calibrating, `0x02` stationary, `0x04` moving, `0x08`
origin, `0x10` stop, and `0x20` TX queue drop.

## Build And Flash

Build `tx.uvprojx` with ARMCC 5.06, then run:

```powershell
& 'C:\Program Files\SEGGER\JLink_V924a\JLink.exe' `
  -CommanderScript 'D:\AIOT\majiang\motion_system\tx_device\flash_v2.jlink'
```

The J-Link script intentionally programs the generated `.hex`. Do not replace
it with the `.axf` in J-Link Commander: the ARMCC compressed RW initialization
image may then be omitted, which prevents BLE advertising from starting.

## Verified On Hardware

- Raw protocol hardware test: 99.78 Hz for 10.59 seconds
- 1058 consecutive frames, zero sequence gaps and zero queue drops
- Every sample timestamp advanced by exactly one 10 ms unit

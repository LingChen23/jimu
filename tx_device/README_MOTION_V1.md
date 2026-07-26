# Motion V1 TX

Open and build:

```text
tx.uvprojx
target: nrf52832_xxaa_lfrc_rx
```

The active firmware keeps NUS for future debug commands and sends formal
motion data through the custom Motion Service:

```text
Motion Service: 6E410001-B5A3-F393-E0A9-E50E24DCCA9E
Motion Data:    6E410002-B5A3-F393-E0A9-E50E24DCCA9E (Notify)
ATT MTU:        185
```

The ICM-20689 is sampled at 100 Hz. Four samples are batched into one fixed
100-byte packet, producing 25 notifications/s. The transmitted IMU values are
the sensor driver's calibrated and board-axis-mapped signed raw values:

```text
acceleration range: +/-8 g, 4096 LSB/g
angular-rate range: +/-1000 deg/s, 32.8 LSB/(deg/s)
sample order: ax, ay, az, gx, gy, gz
```

Packet layout, little-endian:

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | protocol version = 1 |
| 1 | 1 | packet type = 0x10 |
| 2 | 1 | device ID |
| 3 | 1 | packet flags |
| 4 | 4 | session ID |
| 8 | 4 | first sample sequence |
| 12 | 8 | first sample RTC tick at 32768 Hz |
| 20 | 2 | packet sequence |
| 22 | 1 | sample count = 4 |
| 23 | 1 | IMU format = 1 |
| 24 | 56 | four 14-byte IMU samples |
| 80 | 4 | coordinate source sample sequence |
| 84 | 12 | x/y/z in millimetres |
| 96 | 2 | coordinate flags |
| 98 | 2 | reserved = 0 |

Each IMU sample contains a 16-bit tick offset followed by six signed 16-bit
axis values. RTC2 is dedicated to the sample clock, preventing BLE connection
timers from resetting the IMU time base.

The SoftDevice RAM start for this target is `0x20003000`. This leaves enough
reserved RAM for S132 with MTU 185.

## Watchdog

The active main loop owns a 6-second hardware watchdog. On a cold boot it
configures and starts reload channel 0. If a soft reset enters the application
with an already-running watchdog, startup immediately reloads every enabled
channel and adopts the inherited configuration.

Startup reloads occur only between major initialization stages. After the IMU
starts, the watchdog is reloaded only after the main loop successfully reads
and processes a valid IMU sample. A stalled sample interrupt, persistent SPI
read failure, or blocked processing path therefore causes a hardware reset.

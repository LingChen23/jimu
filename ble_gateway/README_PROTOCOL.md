# Gateway USB Protocol

The ESP32-S3 native USB Serial/JTAG CDC channel is bidirectional:

```text
COBS(raw_gateway_frame) + 0x00
```

The decoded frame is little-endian and variable length:

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | frame version = 1 |
| 1 | 1 | gateway ID |
| 2 | 1 | frame type |
| 3 | 1 | reserved = 0 |
| 4 | 4 | gateway sequence |
| 8 | 2 | payload length |
| 10 | N | payload |
| 10+N | 4 | CRC-32/IEEE of all preceding raw bytes |

Frame types:

| Type | Direction | Payload |
| ---: | --- | --- |
| `0x10` | ESP32 → PC | original 100-byte Motion V1 packet |
| `0x20` | PC → ESP32 | device ID + BLE control request |
| `0x21` | ESP32 → PC | device ID + BLE control response |

BLE control request:

```text
[version=1, command, request_id, payload_length, payload...]
```

BLE control response:

```text
[version=1, command|0x80, request_id, result, payload_length, payload...]
```

Commands are `START_STREAM(0x01)`, `STOP_STREAM(0x02)`,
`GET_INFO(0x03)`, `GET_STATUS(0x04)`, `RESET_STATS(0x05)`,
`RECALIBRATE(0x06)`, and `REBOOT(0x07)`.

`GET_STATUS` reports `battery_mV` as the primary battery value. The nRF52832
measures the schematic's `BAT_ADC` divider (VBAT/2 on P0.04/AIN2); `0xFFFF`
means that no valid ADC measurement was available.

CRC uses reflected polynomial `0xEDB88320`, initial value `0xFFFFFFFF`, and
final XOR `0xFFFFFFFF`. This is compatible with Python `zlib.crc32`.

The native USB CDC port is a binary-only business channel after application
startup. USB CDC does not use a physical UART baud rate or 8N1 format.

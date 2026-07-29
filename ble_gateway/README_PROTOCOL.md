# Gateway Business-Channel Protocol

The protocol is bidirectional and identical over direct native USB or the
UART link from a BLE gateway to the SmartBlock aggregator:

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
| `0x30` | ESP32 → PC | 64-byte per-device BLE link diagnostic |
| `0x31` | Aggregator → PC | 100-byte three-link aggregator diagnostic |

The ESP32 sends one version 2 `0x30` diagnostic frame per configured device
per second. The PC decoder also accepts the legacy 44-byte version 1 frame.

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | diagnostic version = 2 |
| 1 | 1 | device ID |
| 2 | 1 | flags: connected / ready / RSSI valid / MTU valid / DLE requested / DLE valid / PHY valid |
| 3 | 1 | signed RSSI in dBm |
| 4 | 2 | actual connection interval in 1.25 ms units |
| 6 | 2 | last NimBLE/HCI disconnect reason |
| 8 | 2 | 1-second receive rate × 100, packets/s |
| 10 | 2 | rolling 10-second receive rate × 100, packets/s |
| 12 | 4 | cumulative valid Motion frames received by ESP32 |
| 16 | 4 | cumulative Motion sequence gaps observed by ESP32 |
| 20 | 4 | cumulative invalid BLE notifications |
| 24 | 4 | successful connection count |
| 28 | 4 | disconnect count |
| 32 | 4 | successful connection-parameter update count |
| 36 | 4 | ESP32 uptime in milliseconds, wrapping at 32 bits |
| 40 | 4 | cumulative ESP32 USB transmit-queue drops |
| 44 | 2 | negotiated ATT MTU |
| 46 | 2 | negotiated maximum TX link-layer payload octets |
| 48 | 2 | negotiated maximum TX link-layer time in microseconds |
| 50 | 2 | negotiated maximum RX link-layer payload octets |
| 52 | 2 | negotiated maximum RX link-layer time in microseconds |
| 54 | 1 | current TX PHY: 1 = 1M, 2 = 2M, 3 = Coded |
| 55 | 1 | current RX PHY: 1 = 1M, 2 = 2M, 3 = Coded |
| 56 | 2 | signed result from the per-connection DLE request |
| 58 | 2 | signed result from the PHY read |
| 60 | 4 | cumulative successful DLE update count |

After every BLE connection, the ESP32 explicitly requests a 251-octet,
2120-microsecond data length and records the negotiated result reported by
NimBLE. MTU, DLE and PHY are tracked independently because a large ATT MTU
does not by itself prove that the link-layer payload length was extended.

The public NimBLE API does not expose the controller's true link-layer
retransmission count. Sequence gaps, receive rate, RSSI and disconnect reasons
are reported instead; sequence gaps must not be labelled as retransmissions.

The three-link aggregator sends one `0x31` diagnostic frame per second with
`gateway_id=0xFF`. Its 100-byte payload is:

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | diagnostic version = 1 |
| 1 | 1 | link count = 3 |
| 2 | 1 | flags: USB ready / output drop observed |
| 3 | 1 | USB output queue high-water mark |
| 4 | 4 | aggregator uptime in milliseconds |
| 8 | 4 | USB RX bytes |
| 12 | 4 | USB TX bytes |
| 16 | 4 | valid PC control requests |
| 20 | 4 | invalid PC input frames |
| 24 | 4 | USB output drops |
| 28 | 24 | gateway A link record |
| 52 | 24 | gateway B link record |
| 76 | 24 | gateway C link record |

Each 24-byte link record is:

| Relative offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | gateway ID |
| 1 | 1 | flags: active / RX queue pending / command queue pending |
| 2 | 1 | aggregator UART controller |
| 3 | 1 | validated-frame queue high-water mark |
| 4 | 4 | UART RX bytes |
| 8 | 4 | valid gateway frames |
| 12 | 2 | COBS errors, saturated at 65535 |
| 14 | 2 | CRC errors, saturated at 65535 |
| 16 | 2 | format errors, saturated at 65535 |
| 18 | 2 | complete input/output frame drops, saturated at 65535 |
| 20 | 2 | command UART output drops, saturated at 65535 |
| 22 | 2 | last valid frame age in 100 ms units; `0xFFFF` means never |

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
startup. USB CDC does not use a physical UART baud rate or 8N1 format. The
gateway-to-aggregator UART variant uses 921600 baud, 8N1, full duplex and
forwards these same delimited frames without changing the gateway ID,
gateway sequence or CRC.

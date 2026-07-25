# Gateway UART Protocol

The ESP32-S3 forwards every valid nRF52832 Motion V1 packet without modifying
its 100-byte payload. The business UART uses `921600 8N1` and emits:

```text
COBS(raw_gateway_frame) + 0x00
```

The decoded raw gateway frame is 114 bytes, little-endian:

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | frame version = 1 |
| 1 | 1 | gateway ID = 0 |
| 2 | 1 | frame type = `0x10` Motion Data |
| 3 | 1 | reserved = 0 |
| 4 | 4 | gateway sequence |
| 8 | 2 | payload length = 100 |
| 10 | 100 | original nRF Motion V1 payload |
| 110 | 4 | CRC-32/IEEE of bytes 0..109 |

CRC uses reflected polynomial `0xEDB88320`, initial value `0xFFFFFFFF`, and
final XOR `0xFFFFFFFF`. This is compatible with Python `zlib.crc32`.

UART0 is a binary-only business channel after application startup. Runtime
text logs are disabled. The PC decoder uses the zero delimiter, COBS length,
frame header and CRC to recover after reset or malformed input.

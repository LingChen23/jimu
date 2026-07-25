# ESP32-S3 Multi Trajectory Receiver

Each configured TX uses an independent Nordic UART Service client. The fixed
20-byte trajectory frame is unchanged:

```text
0       0xA5
1       device_id
2..3    sequence (uint16 little-endian)
4..7    x_mm (int32 little-endian)
8..11   y_mm (int32 little-endian)
12..15  z_mm (int32 little-endian)
16..17  sample_tick_10ms (uint16 little-endian)
18      flags
19      0x5A
```

Frames are routed by BLE connection handle and then checked against the
`expected_device_id` configured for that address. Sequence-loss counters are
therefore independent for every TX.

The serial line remains compatible with the existing parser:

```text
I (...) mahjong_data: [addr=E0:CF:E0:E7:F7:4B conn=1 id=1 seq=42 tick=84 flags=0x04] x=12 y=-3 z=45
```

Use `460800` baud for multi-device operation.

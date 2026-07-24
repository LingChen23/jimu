# TX BLE-only test

This firmware tests only the nRF52832 BLE peripheral and Nordic UART Service
(NUS) notification path. It does not initialize the IMU, trajectory algorithm,
UART, RTT, NFC, or low-power workflow.

Runtime behavior:

- Advertises as the existing `Mahjong` device with the existing NUS UUIDs.
- Waits for the ESP32-S3 central to connect and enable notifications.
- Sends `sequence,0,0\r\n` at 50 Hz.
- Increments `sequence` only after a notification is accepted by SoftDevice.
- Treats S132 v3 `BLE_ERROR_NO_TX_PACKETS` as normal transmit backpressure.
- Feeds a watchdog left running by an earlier firmware image.

Expected ESP32 output:

```text
mahjong_data: [addr=E0:CF:E0:E7:F7:4B ...] x=0 y=0 z=0
mahjong_data: [addr=E0:CF:E0:E7:F7:4B ...] x=1 y=0 z=0
```

## Verified result

- ARMCC build: 0 errors, 0 warnings.
- J-Link programming and verification: successful.
- ESP32 COM12 continuous test: 3322 frames in 65 seconds.
- Sequence range: 1120 through 4441, with no gaps or restarts.
- BLE disconnects: 0.
- Measured receive rate: 51.09 Hz.

The first test reset periodically because S132 v3 reports a full notification
queue as `BLE_ERROR_NO_TX_PACKETS` (`0x3004`). That condition is now treated as
normal backpressure and retried on the next 20 ms loop.

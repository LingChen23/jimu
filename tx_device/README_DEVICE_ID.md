# TX Device ID

Open `tx.uvprojx` in Keil and edit:

```text
Application -> tx_device_config.h
```

Change only this line before building each physical TX:

```c
#define TX_DEVICE_ID 1U
```

Use a unique value from 1 through 255 for every TX. Do not use 0.

The device ID no longer changes connection-parameter behavior. Every device,
including device 1, requests the 30 ms preferred interval approximately five
seconds after connecting. All devices explicitly use +4 dBm BLE transmit
power.

The value is used in trajectory frame byte 1 and is synchronized to
`sys_config.no` before BLE advertising starts. The Flash configuration is
updated only when the configured ID changes, so normal resets do not erase and
rewrite the configuration page.

The matching ESP32 target entry must use the same value:

```c
.expected_device_id = 1,
```

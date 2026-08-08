# TX Device ID

Open `tx.uvprojx` in Keil and edit:

```text
Application -> tx_device_config.h
```

Change only this line before building each physical TX:

```c
#define TX_DEVICE_ID 1U
```

Use a unique value from 1 through 12 for every TX. Do not use 0.

Firmware 0.2.2 selects the steady connection interval from the device ID and
requests it approximately five seconds after connecting:

```text
devices 1..4: 31.25 ms
devices 5..8: 32.5 ms
devices 9..12:  35 ms
```

All devices explicitly use +4 dBm BLE transmit power.

The value is used in trajectory frame byte 1 and is synchronized to
`sys_config.no` before BLE advertising starts. The Flash configuration is
updated only when the configured ID changes, so normal resets do not erase and
rewrite the configuration page.

The matching ESP32 target entry must use the same value:

```c
.expected_device_id = 1,
```

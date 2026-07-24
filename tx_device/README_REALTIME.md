# TX Online Inertial Trajectory Firmware

## Runtime

- ICM-20689 sampling: 100 Hz, accelerometer `+/-4 g`, gyroscope `+/-500 dps`.
- Sensor bandwidth: approximately 21 Hz accelerometer and 20 Hz gyroscope.
- Startup requires two seconds of continuous stationary data.
- A 61-sample centered SHOE-style window gives a fixed output latency of about
  300 ms. This is continuous online output, not end-of-motion batch processing.
- Navigation uses quaternion strapdown integration, a 15-state error covariance,
  adaptive accelerometer attitude feedback, and confirmed zero-velocity updates.
- Every segment emits an exact `(0,0,0)` origin and 50 Hz moving points.
- Candidate stationary samples continue normal propagation and 50 Hz output.
- Only 20 consecutive stationary decisions may trigger ZUPT and one `STOP`
  frame. ZUPT updates velocity, attitude, and sensor biases, but never changes
  the already published position or forces the endpoint to the origin.
- Stationarity now uses window variance plus acceleration-norm and mean-gyro
  checks, followed by 20 consecutive confirmations.
- Three consecutive saturated samples, a non-finite state, speed above 5 m/s,
  or per-axis displacement above 10 m rejects the segment and restarts the
  two-second stationary calibration. These are hand-motion safety limits, not
  a fixed movement-duration timeout.
- There is no movement timeout, terminal position pullback, velocity decay,
  high-pass trajectory shaping, or post-processing of previous points.

The device-specific acceleration calibration matrix and offset are near the
top of `src/realtime_traj.c`. They were generated from a strict six-face pass
and accepted by a second independent six-face validation on 2026-07-22.
Startup calibration uses those fixed scale/offset values and only establishes
initial attitude and gyroscope bias; it no longer applies an orientation-
dependent scalar acceleration correction.

## BLE frame

```text
0       0xA5
1       device_id (sys_config.no, with 1 as the unset fallback)
2..3    sequence
4..7    x_mm (int32)
8..11   y_mm (int32)
12..15  z_mm (int32)
16..17  sample_tick_10ms
18      flags
19      0x5A
```

All multi-byte values are little-endian. Flags report origin, moving, stop,
queue drop, sample gap, and unreliable/saturated input.

## Build

```powershell
& 'D:\APP\STM32\APP\MDK528\UV4\UV4.exe' -b tx.uvprojx -j0
```

Firmware output: `_build\nrf52832_xxaa_lfrc_rx.hex`.

## Physical limitation

Without an external position reference, position and yaw errors remain
unobservable during long uninterrupted motion. Stationary ZUPT limits velocity
drift and stops endpoint extension, but cannot guarantee an exact displacement.

# Realtime Trajectory Estimator V3

## Coordinate convention

- `q` rotates a body-frame vector into the world frame.
- World `+Z` is opposite gravity and is established during calibration.
- World yaw starts at zero because the six-axis ICM-20689 has no absolute yaw
  reference.

## Estimator stages

1. Keep the device stationary for two seconds. Average acceleration establishes
   world vertical and a scalar acceleration correction. Average angular rate
   establishes the three-axis gyro bias.
2. While stationary, use gravity feedback to correct quaternion roll and pitch,
   and slowly track gyro bias and acceleration scale.
3. While moving, propagate the quaternion from bias-corrected gyro data. Do not
   use acceleration as a tilt reference because translational acceleration is
   not gravity.
4. Rotate measured acceleration into the world frame, subtract world gravity,
   low-pass filter it, apply a deadband, and integrate with trapezoidal steps.
5. Detect movement before stationary gravity correction, so translational
   acceleration is not absorbed as a false tilt change.
6. Use a 30-sample six-axis window plus 20 confirmation samples for a
   zero-velocity update and return the next stationary origin to `(0, 0, 0)`.
7. Correct terminal velocity drift without forcing the physical endpoint to
   zero. Limit correction to 80% of the measured segment peak on each axis.
8. Apply a 0.5-second weak-motion holdoff after STOP and a 20-second movement
   timeout to prevent repeated zero-length segments and unbounded integration.

## Transport

- IMU sampling and raw six-axis notifications are both 100 Hz.
- Frames remain fixed at 20 bytes.
- The TX queue holds 64 frames and the ESP32 connection interval is 15 ms.
- The computer performs a second calibration, quaternion propagation, gravity
  removal, and integration from the transmitted raw samples. At STOP it removes
  the constant world-acceleration bias required to make final velocity zero and
  reintegrates the complete segment without forcing final position to zero.

## Physical limit

A six-axis IMU cannot distinguish rest from perfectly constant velocity and has
no absolute position or yaw reference. ZUPT bounds short start-stop gestures,
but long-duration absolute trajectories require an external reference such as
UWB, optical tracking, or wheel/contact constraints.

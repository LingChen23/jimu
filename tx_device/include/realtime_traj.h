#ifndef REALTIME_TRAJ_H
#define REALTIME_TRAJ_H

#include <stdint.h>

#define REALTIME_TRAJ_SAMPLE_HZ       100U
#define REALTIME_TRAJ_OUTPUT_HZ       50U
#define REALTIME_TRAJ_LAG_SAMPLES     61U
#define REALTIME_TRAJ_ERROR_STATE_DIM 15U

typedef enum
{
    REALTIME_TRAJ_CALIBRATING = 0,
    REALTIME_TRAJ_STATIONARY,
    REALTIME_TRAJ_MOVING
} realtime_traj_state_t;

typedef enum
{
    REALTIME_TRAJ_NO_OUTPUT = 0,
    REALTIME_TRAJ_ORIGIN,
    REALTIME_TRAJ_POINT,
    REALTIME_TRAJ_STOP,
    REALTIME_TRAJ_ABORT
} realtime_traj_event_t;

typedef struct
{
    float accel[3];
    float gyro[3];
    float dt_seconds;
    uint16_t tick_10ms;
    uint8_t quality_flags;
} realtime_traj_sample_t;

typedef struct
{
    realtime_traj_state_t state;
    float q[4];
    float gyro_bias[3];
    float accel_bias[3];
    float accel_scale;
    float velocity[3];
    float position[3];
    float covariance[REALTIME_TRAJ_ERROR_STATE_DIM *
                     REALTIME_TRAJ_ERROR_STATE_DIM];
    float transition[REALTIME_TRAJ_ERROR_STATE_DIM *
                     REALTIME_TRAJ_ERROR_STATE_DIM];
    float matrix_tmp[REALTIME_TRAJ_ERROR_STATE_DIM *
                     REALTIME_TRAJ_ERROR_STATE_DIM];
    float calibration_accel_sum[3];
    float calibration_gyro_sum[3];
    realtime_traj_sample_t lag_samples[REALTIME_TRAJ_LAG_SAMPLES];
    uint16_t calibration_count;
    uint8_t lag_head;
    uint8_t lag_count;
    uint8_t motion_confirm_count;
    uint8_t stationary_confirm_count;
    uint8_t invalid_sample_count;
    uint8_t output_divider;
    uint8_t quality_flags;
} realtime_traj_t;

void realtime_traj_init(realtime_traj_t* traj);

uint8_t realtime_traj_take_quality_flags(realtime_traj_t* traj);

realtime_traj_event_t realtime_traj_process(realtime_traj_t* traj,
                                            const int16_t raw[6],
                                            float dt_seconds,
                                            uint16_t input_tick_10ms,
                                            float position_m[3],
                                            uint16_t* output_tick_10ms);

#endif

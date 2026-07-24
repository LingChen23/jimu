#ifndef CALC_TRAJ_H
#define CALC_TRAJ_H

#include "calc_common_func.h"

#define MATH_PI                     3.141592653589793
#define GRAVITY                     9.81f

typedef struct {
    int32_t             max_value;          // max value of data, default 32768
    int16_t             a_range;            // data range of acceleration, default 4, unit g(m/s2)
    int16_t             w_range;            // data range of angular velocity, default 2000, unit °/s
    int16_t             fs;                 // sampling frequency, default 200, unit Hz
    float               ts;                 // sampling time, default 10, unit second
    int32_t             num_point_cali;     // number of calibration buffer, default fs * ts = 2000
    int32_t             num_point_judge;    // number of judging if stable, default fs / 3 = 66
    float               var_acc;            // variance of acc threshold for stable state
    float               var_gyro;           // variance of gyro threshold for stable state
    float               kp_stable;
    float               kp_instable;
} data_paras_traj;

typedef enum {
    TRAJ_PRAPARE = 0x00,
    TRAJ_STABLE,
    TRAJ_ACTION,
    TRAJ_OUTPUT,
} traj_state;

int8_t get_traj(float*            data_buffer,
                int16_t           data_index,
                data_paras_traj*  p,
                traj_state*       data_state,
                float*            data_output_realtime,
                float*            data_output_cali,
                int32_t*          data_output_cali_len,
                float*            amp_vector,
                float*            amp_sum
                );

#endif

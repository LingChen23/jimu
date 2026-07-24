#ifndef _TRAJ_PROT_H
#define _TRAJ_PROT_H

#include "stdint.h"

typedef struct
{
    uint8_t  sn[10];
    uint8_t  model[10];
    uint8_t  fw_ver[10];
    uint8_t  bat_level;
    uint32_t utc_time;
    float    traj_amp_sum;
    float    traj_amp_vector;
    uint32_t raw_wave_sample_rate;
    uint32_t raw_wave_total_size;
    // raw wave float: x1 y1 z1, x2 y2 z2, ... , xn yn zn
} traj_prot_t;

#endif
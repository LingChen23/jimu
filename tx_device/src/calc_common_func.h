#ifndef CALC_COMMON_FUNC_H
#define CALC_COMMON_FUNC_H

#include "stdint.h"
#include "stdlib.h"
#include "string.h"
#include <stdio.h>
#include <math.h>

int8_t get_mean(int16_t* data, int32_t len, int32_t interval, float* data_mean);
int8_t get_mean32(float* data, int32_t len, int32_t interval, float* data_mean);
int8_t get_var(int16_t* data, int32_t len, int32_t interval, float* data_var, int8_t scale);
int8_t get_var32(float* data, int32_t len, int32_t interval, float* data_var, int8_t scale);
int8_t get_norm(int16_t* data, int32_t len, int32_t interval, float* data_norm);
int8_t get_norm32(float* data, int32_t len, int32_t interval, float* data_norm);

int8_t quatern_prod(float* a, float* b, float* ab);
int8_t quatern_conj(float* q, float* q_conj);
int8_t quatern_rot(float* v, float* q, float* qvq_1);
int8_t quatern_2_quatern(float* e, float* q);
int8_t update_imu(float* q, float* data_a, float* data_w, float fs, float kp);


#endif // CALC_COMMON_FUNC_H

#include "calc_common_func.h"

/**
 * @brief get mean of data
 *
 * @param data original data
 * @param len length of data
 * @param data_mean result of mean
 */
int8_t get_mean(int16_t* data, int32_t len, int32_t interval, float* data_mean) {
    int32_t i = 0;

    *data_mean = 0;
    for(i = 0;i < len;i ++) {
        (*data_mean) += *(data + i * interval);
    }
    (*data_mean) /= len;

    return 0;
}

/**
 * @brief get mean of float data
 *
 * @param data original data
 * @param len length of data
 * @param data_mean result of mean
 */
int8_t get_mean32(float* data, int32_t len, int32_t interval, float* data_mean) {
    int32_t i = 0;

    *data_mean = 0;
    for(i = 0;i < len;i ++) {
        (*data_mean) += *(data + i * interval);
    }
    (*data_mean) /= len;

    return 0;
}

/**
 * @brief get variance of data
 *
 * @param data original data
 * @param len length of data
 * @param data_mean result of variance
 * @param scale scale for data to not overflow
 */
int8_t get_var(int16_t* data, int32_t len, int32_t interval, float* data_var, int8_t scale) {
    int32_t i = 0;
    float data_mean = 0;

    get_mean(data, len, interval, &data_mean);

    *data_var = 0;
    for(i = 0;i < len;i ++) {
        (*data_var) += ((*(data + i * interval) - data_mean) / scale * (*(data + i * interval) - data_mean) / scale);
    }
    (*data_var) /= len;

    return 0;
}

/**
 * @brief get variance of float data
 *
 * @param data original data
 * @param len length of data
 * @param data_mean result of variance
 * @param scale scale for data to not overflow
 */
int8_t get_var32(float* data, int32_t len, int32_t interval, float* data_var, int8_t scale) {
    int32_t i = 0;
    float data_mean = 0;

    get_mean32(data, len, interval, &data_mean);

    *data_var = 0;
    for(i = 0;i < len;i ++) {
        (*data_var) += ((*(data + i * interval) - data_mean) / scale * (*(data + i * interval) - data_mean) / scale);
    }
    (*data_var) /= len;

    return 0;
}

/**
 * @brief get norm of data int16_t
 *
 * @param data original data
 * @param len length of data
 * @param data_norm result of norm
 */
int8_t get_norm(int16_t* data, int32_t len, int32_t interval, float* data_norm) {
    int32_t i = 0;

    *data_norm = 0;
    for(i = 0;i < len;i ++) {
        (*data_norm) += (*(data + i * interval)) * (*(data + i * interval));
    }
    (*data_norm) = sqrt(*data_norm);

    return 0;
}

/**
 * @brief get norm of data float data
 *
 * @param data original data
 * @param len length of data
 * @param data_norm result of norm
 */
int8_t get_norm32(float* data, int32_t len, int32_t interval, float* data_norm) {
    int32_t i = 0;

    *data_norm = 0;
    for(i = 0;i < len;i ++) {
        (*data_norm) += (*(data + i * interval)) * (*(data + i * interval));
    }
    (*data_norm) = sqrt(*data_norm);

    return 0;
}


/*************************Quatern Part***********************/
/*************************Quatern Part***********************/
int8_t quatern_prod(float* a, float* b, float* ab) {
    (*(ab+0)) = (*(a+0))*(*(b+0))-(*(a+1))*(*(b+1))-(*(a+2))*(*(b+2))-(*(a+3))*(*(b+3));
    (*(ab+1)) = (*(a+0))*(*(b+1))+(*(a+1))*(*(b+0))+(*(a+2))*(*(b+3))-(*(a+3))*(*(b+2));
    (*(ab+2)) = (*(a+0))*(*(b+2))-(*(a+1))*(*(b+3))+(*(a+2))*(*(b+0))+(*(a+3))*(*(b+1));
    (*(ab+3)) = (*(a+0))*(*(b+3))+(*(a+1))*(*(b+2))-(*(a+2))*(*(b+1))+(*(a+3))*(*(b+0));

    return 0;
}

int8_t quatern_conj(float* q, float* q_conj) {
    (*(q_conj + 0)) = (*(q + 0));
    (*(q_conj + 1)) = -(*(q + 1));
    (*(q_conj + 2)) = -(*(q + 2));
    (*(q_conj + 3)) = -(*(q + 3));

    return 0;
}

int8_t quatern_rot(float* v, float* q, float* qvq_1) {
    float v_temp[4];
    float qv[4];
    float q_conj[4];
    float qvq_[4];
    v_temp[0] = 0;
    v_temp[1] = (*(v + 0));
    v_temp[2] = (*(v + 1));
    v_temp[3] = (*(v + 2));

    quatern_prod(q, v_temp, qv);
    quatern_conj(q, q_conj);
    quatern_prod(qv, q_conj, qvq_);

    (*(qvq_1+0)) = qvq_[1];
    (*(qvq_1+1)) = qvq_[2];
    (*(qvq_1+2)) = qvq_[3];

    return 0;
}

int8_t quatern_2_quatern(float* e, float* q) {
    *(q+0) = cos(*(e+1) / 2) * cos(*(e+2) / 2) * cos(*(e+0) / 2) - sin(*(e+1) / 2) * sin(*(e+2) / 2) * sin(*(e+0) / 2);
    *(q+1) = sin(*(e+1) / 2) * sin(*(e+2) / 2) * cos(*(e+0) / 2) + cos(*(e+1) / 2) * cos(*(e+2) / 2) * sin(*(e+0) / 2);
    *(q+2) = sin(*(e+1) / 2) * cos(*(e+2) / 2) * cos(*(e+0) / 2) + cos(*(e+1) / 2) * sin(*(e+2) / 2) * sin(*(e+0) / 2);
    *(q+3) = cos(*(e+1) / 2) * sin(*(e+2) / 2) * cos(*(e+0) / 2) - sin(*(e+1) / 2) * cos(*(e+2) / 2) * sin(*(e+0) / 2);

    return 0;
}

int8_t update_imu(float* q, float* data_a, float* data_w, float fs, float kp) {
    float data_norm = 0;
    float data_a_temp[3] = {0,};
    float v[3] = {0, };
    float error[3] = {0, };
    float ref[3] = {0, };
    float q_ref[4] = {0, };
    float q_new[4] = {0, };
    int32_t i = 0;

    get_norm32(data_a, 3, 1, &data_norm);
    for(i = 0;i < 3;i ++) {
        data_a_temp[i] = (*(data_a + i)) / data_norm;
    }
    v[0] = 2 * ((*(q + 1)) * (*(q + 3)) - (*(q + 0)) * (*(q + 2)));
    v[1] = 2 * ((*(q + 0)) * (*(q + 1)) + (*(q + 2)) * (*(q + 3)));
    v[2] = (*(q + 0)) * (*(q + 0)) - (*(q + 1)) * (*(q + 1)) - (*(q + 2)) * (*(q + 2)) + (*(q + 3)) * (*(q + 3));

    error[0] = v[1] * data_a_temp[2] - v[2] * data_a_temp[1];
    error[1] = v[2] * data_a_temp[0] - v[0] * data_a_temp[2];
    error[2] = v[0] * data_a_temp[1] - v[1] * data_a_temp[0];

    for(i = 0;i < 3;i ++) {
        ref[i] = ((*(data_w + i)) - kp * error[i]) / fs;
    }

//    quatern_2_quatern(ref, q_ref);
//    quatern_prod(q, q_ref, q_new);

    for(i = 0;i < 3;i ++) q_ref[i + 1] = ref[i] * fs;
    quatern_prod(q, q_ref, q_new);
    for(i = 0;i < 4;i ++) q_new[i] = *(q + i) + q_new[i] / fs / 2;

    get_norm32(q_new, 4, 1, &data_norm);
    for(i = 0;i < 4;i ++) {
        (*(q + i)) = (*(q_new + i)) / data_norm;
    }

    return 0;
}
/*************************Quatern Part***********************/
/*************************Quatern Part***********************/

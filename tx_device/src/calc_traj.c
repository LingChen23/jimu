#include "calc_traj.h"

/**
 * @brief get action flag of data, 0 for stable, 1 for action
 *
 * @param data original data
 * @param action_flag action flag of data with length (LEN_OF_WINDOW * 1)
 * @return int8_t
 */
int8_t get_stable(float*              data_buffer,
                  data_paras_traj*    p,
                  int8_t*             is_stable) {
    float data_var[6] = {0, };
    int32_t i = 0, j = 0;

    *is_stable = 1;

    for(i = 0;i < 6;i ++) {
        get_var32(data_buffer + i, p->num_point_judge, 6, data_var + i, 1);
    }
    for(j = 0;j < 3;j ++) {
        (*is_stable) &= (data_var[j] < p->var_acc);
        (*is_stable) &= (data_var[3 + j] < p->var_gyro);
    }
		//printf("\n%f, %f, %f, %f, %f, %f\n", data_var[0], data_var[1], data_var[2], data_var[3], data_var[4], data_var[5]);

    return 0;
}


int8_t get_traj_cali(float*              data_output_cali,
                     int32_t*            data_output_cali_len,
                     data_paras_traj*    p) {
    float v_error[3] = {0, };
    float c[3] = {0, };
    int32_t i = 0, j = 0;

    for (i = 0;i < 3;i ++) {
        v_error[i] = *(data_output_cali + *data_output_cali_len * 3 + i) / *data_output_cali_len;
    }

    for (i = 0;i < *data_output_cali_len;i ++) {
        for (j = 0;j < 3;j ++){
            *(data_output_cali + i * 3 + j) -= (v_error[j] * (i + 1));
            c[j] += *(data_output_cali + i * 3 + j) / p->fs;
            *(data_output_cali + i * 3 + j) = c[j];
        }
    }

    return 0;
}

/**
 * @brief get real-time trajectory point according to original data
 *
 * @param data_buffer           original acceleration and gyro data, 6 * int16_t
 * @param data_stable           the flag of judging the data is stable or not
 * @param data_index            the head's index of data in the circle data_buffer
 * @param a_stable              the zero-bias error of acceleration
 * @param a_stable_buffer       the history stable value of acceleration
 * @param a_stable_buffer_len   the length of history
 * @param w_stable              the zero-bias error of angular velocity
 * @param q                     the quatern value
 * @param v                     the velocity
 * @param c                     the coordinate
 */
int8_t get_traj_point(float*              data_buffer,
                      int16_t             data_index,
                      data_paras_traj*    p,
                      float*              q,
                      int8_t              is_stable,
                      float*              data_output_realtime,
                      float*              data_output_cali,
                      int32_t*            data_output_cali_len) {

    float data_a[3] = {0,};
    float data_w[3] = {0,};
    float data_ag_temp32[3] = {0,};
    int16_t i = 0;

    for(i = 0;i < 3;i ++) {
        data_a[i] = *(data_buffer + data_index * 6 + i);
        if(!is_stable) {
            data_w[i] = *(data_buffer + data_index * 6 + i + 3);
        }
    }
    if (is_stable) {
        update_imu(q, data_a, data_w, p->fs, p->kp_stable);
        for(i = 0;i < 3;i ++) {
            *(data_output_realtime + i) = 0;
        }

    } else {
        update_imu(q, data_a, data_w, p->fs, p->kp_instable);
        quatern_rot(data_a, q, data_ag_temp32);
        data_ag_temp32[2] -= GRAVITY;
        for(i = 0;i < 3;i ++) {
            if (*data_output_cali_len == 0) {
                *(data_output_realtime + i) = 0;
                *(data_output_cali + i) = data_ag_temp32[i] / p->fs;
            } else {
                if (*data_output_cali_len < p->num_point_cali) {
                    (*(data_output_realtime + i)) += *(data_output_cali + (*data_output_cali_len - 1) * 3 + i) / p->fs;
                    *(data_output_cali + *data_output_cali_len * 3 + i) = *(data_output_cali + (*data_output_cali_len - 1) * 3 + i) + data_ag_temp32[i] / p->fs;
                }
            }
        }
        (*data_output_cali_len) += 1;
    }


    return 0;
}



/**
 * @brief get trajectory point according to original data
 *
 * @param data_input                original acceleration and gyro data, 6 * int16_t
 * @param data_is_output            the flag of judging the output is valid or not, bool
 * @param data_output               trajectory point, 3 * float
 * @param data_output_cali          calibrated trajectory data
 * @param data_output_cali_len      the index of calied data
 * @param amp_vector				the amplitude of final point
 * @param amp_sum					the amplitude of whole path
 * @param q							the attitude of IMU
 */
int8_t get_traj(float*            data_buffer,
                int16_t           data_index,
                data_paras_traj*  p,
                traj_state*       data_state,
                float*            data_output_realtime,
                float*            data_output_cali,
                int32_t*          data_output_cali_len,
                float*            amp_vector,
                float*            amp_sum
                ) {
    static float q[4] = {1, 0, 0, 0};
    int32_t i = 0;
    int8_t is_stable = 0;

    *amp_vector = 0;
    *amp_sum = 0;

    get_stable(data_buffer, p, &is_stable);
    get_traj_point(data_buffer, data_index, p, q, is_stable, data_output_realtime, data_output_cali, data_output_cali_len);

    if (is_stable) {
        if (*data_output_cali_len > p->num_point_judge && *data_output_cali_len < p->num_point_cali + p->num_point_judge) {
            (*data_output_cali_len) -= p->num_point_judge;
            get_traj_cali(data_output_cali, data_output_cali_len, p);

            if (*data_output_cali_len > 0) {
                for (i = 1;i < *data_output_cali_len;i ++) {
                    (*amp_sum) += sqrt(pow(*(data_output_cali + i * 3 + 0) - *(data_output_cali + (i - 1) * 3 + 0), 2)
                                        + pow(*(data_output_cali + i * 3 + 1) - *(data_output_cali + (i - 1) * 3 + 1), 2)
                                        + pow(*(data_output_cali + i * 3 + 2) - *(data_output_cali + (i - 1) * 3 + 2), 2));
                    if (i == (*data_output_cali_len) - 1) {
                         (*amp_vector) = sqrt(pow(*(data_output_cali + i * 3 + 0), 2)
                                         + pow(*(data_output_cali + i * 3 + 1), 2)
                                         + pow(*(data_output_cali + i * 3 + 2), 2));
                    }
                }
            }
            *data_state = TRAJ_OUTPUT;
        } else {
            *data_state = TRAJ_STABLE;
        }
    } else {
        *data_state = TRAJ_ACTION;
    }

    return 0;
}

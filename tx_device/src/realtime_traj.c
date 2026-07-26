#include "realtime_traj.h"
#include "trajectory_protocol.h"

#include <math.h>
#include <string.h>

#define GRAVITY_MPS2                9.80665f
#define ACCEL_SCALE_MPS2            (8.0f * GRAVITY_MPS2 / 32768.0f)
#define GYRO_SCALE_RADPS            (1000.0f * 3.14159265358979323846f / \
                                     (180.0f * 32768.0f))
#define NOMINAL_DT_SECONDS          0.01f
#define CALIBRATION_SAMPLES         200U
#define MOTION_CONFIRM_SAMPLES      2U
#define STOP_CONFIRM_SAMPLES        20U
#define SHOE_ACCEL_NOISE            0.12f
#define SHOE_GYRO_NOISE             0.015f
#define SHOE_STATIONARY_THRESHOLD   8.0f
#define STATIC_ACCEL_NORM_TOLERANCE 0.80f
#define STATIC_GYRO_MEAN_LIMIT      0.15f
#define MAX_INVALID_SAMPLES         3U
#define MAX_NAVIGATION_SPEED_MPS    5.0f
#define MAX_NAVIGATION_POSITION_M   10.0f
#define ZUPT_VELOCITY_NOISE         0.025f
#define ACCEL_PROCESS_NOISE         0.10f
#define GYRO_PROCESS_NOISE          0.008f
#define ACCEL_BIAS_RANDOM_WALK      0.004f
#define GYRO_BIAS_RANDOM_WALK       0.0004f
#define STATIC_GYRO_BIAS_RATE       0.01f
#define STATIC_ACCEL_BIAS_RATE      0.0005f
#define STATIC_ATTITUDE_GAIN        2.5f
#define MOVING_ATTITUDE_GAIN        0.12f
#define MOVING_ACCEL_NORM_GATE      0.35f
#define MOVING_ACCEL_ANGLE_GATE_RAD 0.1745329252f
#define MAX_ACCEL_BIAS              1.5f
#define MAX_GYRO_BIAS               0.35f
#define MATRIX_DIM                  REALTIME_TRAJ_ERROR_STATE_DIM
#define MATRIX_SIZE                 (MATRIX_DIM * MATRIX_DIM)

#define STATE_POSITION 0U
#define STATE_VELOCITY 3U
#define STATE_ATTITUDE 6U
#define STATE_ACCEL_BIAS 9U
#define STATE_GYRO_BIAS 12U

#define MAT_INDEX(row, column) ((row) * MATRIX_DIM + (column))

/* Device-specific six-face calibration, verified on 2026-07-22. */
static const float m_accel_calibration_matrix[9] = {
    1.001725078f, 0.0f, 0.0f,
    0.0f, 1.001929374f, 0.0f,
    0.0f, 0.0f, 0.986569848f
};

static const float m_accel_calibration_offset[3] = {
    0.167142211f, -0.035488054f, 0.159343099f
};

static float clamp_float(float value, float minimum, float maximum)
{
    if (value < minimum) {
        return minimum;
    }
    if (value > maximum) {
        return maximum;
    }
    return value;
}

static float vector_norm(const float value[3])
{
    return sqrtf(value[0] * value[0] + value[1] * value[1] +
                 value[2] * value[2]);
}

static void copy_position(const realtime_traj_t* traj, float position_m[3])
{
    memcpy(position_m, traj->position, sizeof(traj->position));
}

static void normalize_quaternion(float q[4])
{
    float norm = sqrtf(q[0] * q[0] + q[1] * q[1] +
                       q[2] * q[2] + q[3] * q[3]);

    if (norm < 0.000001f) {
        q[0] = 1.0f;
        q[1] = 0.0f;
        q[2] = 0.0f;
        q[3] = 0.0f;
        return;
    }

    q[0] /= norm;
    q[1] /= norm;
    q[2] /= norm;
    q[3] /= norm;
}

static void quaternion_from_gravity(const float accel[3], float q[4])
{
    float accel_norm = vector_norm(accel);
    float ax;
    float ay;
    float az;

    if (accel_norm < 0.001f) {
        q[0] = 1.0f;
        q[1] = 0.0f;
        q[2] = 0.0f;
        q[3] = 0.0f;
        return;
    }

    ax = accel[0] / accel_norm;
    ay = accel[1] / accel_norm;
    az = accel[2] / accel_norm;

    if (az < -0.999f) {
        q[0] = 0.0f;
        q[1] = 1.0f;
        q[2] = 0.0f;
        q[3] = 0.0f;
        return;
    }

    q[0] = 1.0f + az;
    q[1] = ay;
    q[2] = -ax;
    q[3] = 0.0f;
    normalize_quaternion(q);
}

static void quaternion_zero_yaw(float q[4])
{
    float sin_roll = 2.0f * (q[0] * q[1] + q[2] * q[3]);
    float cos_roll = 1.0f - 2.0f * (q[1] * q[1] + q[2] * q[2]);
    float sin_pitch = 2.0f * (q[0] * q[2] - q[3] * q[1]);
    float roll = atan2f(sin_roll, cos_roll);
    float pitch = asinf(clamp_float(sin_pitch, -1.0f, 1.0f));
    float half_roll = 0.5f * roll;
    float half_pitch = 0.5f * pitch;
    float cr = cosf(half_roll);
    float sr = sinf(half_roll);
    float cp = cosf(half_pitch);
    float sp = sinf(half_pitch);

    q[0] = cr * cp;
    q[1] = sr * cp;
    q[2] = cr * sp;
    q[3] = -sr * sp;
    normalize_quaternion(q);
}

static void quaternion_integrate(float q[4],
                                 const float angular_rate[3],
                                 float dt_seconds)
{
    float qw = q[0];
    float qx = q[1];
    float qy = q[2];
    float qz = q[3];
    float half_dt = 0.5f * dt_seconds;

    q[0] += (-qx * angular_rate[0] - qy * angular_rate[1] -
             qz * angular_rate[2]) * half_dt;
    q[1] += (qw * angular_rate[0] + qy * angular_rate[2] -
             qz * angular_rate[1]) * half_dt;
    q[2] += (qw * angular_rate[1] - qx * angular_rate[2] +
             qz * angular_rate[0]) * half_dt;
    q[3] += (qw * angular_rate[2] + qx * angular_rate[1] -
             qy * angular_rate[0]) * half_dt;
    normalize_quaternion(q);
}

static void quaternion_apply_error(float q[4], const float error[3])
{
    float original[4] = { q[0], q[1], q[2], q[3] };
    float ex = 0.5f * error[0];
    float ey = 0.5f * error[1];
    float ez = 0.5f * error[2];

    q[0] = original[0] - original[1] * ex -
           original[2] * ey - original[3] * ez;
    q[1] = original[1] + original[0] * ex +
           original[2] * ez - original[3] * ey;
    q[2] = original[2] + original[0] * ey -
           original[1] * ez + original[3] * ex;
    q[3] = original[3] + original[0] * ez +
           original[1] * ey - original[2] * ex;
    normalize_quaternion(q);
}

static void rotation_matrix_from_quaternion(const float q[4], float r[9])
{
    float qw = q[0];
    float qx = q[1];
    float qy = q[2];
    float qz = q[3];

    r[0] = 1.0f - 2.0f * (qy * qy + qz * qz);
    r[1] = 2.0f * (qx * qy - qw * qz);
    r[2] = 2.0f * (qx * qz + qw * qy);
    r[3] = 2.0f * (qx * qy + qw * qz);
    r[4] = 1.0f - 2.0f * (qx * qx + qz * qz);
    r[5] = 2.0f * (qy * qz - qw * qx);
    r[6] = 2.0f * (qx * qz - qw * qy);
    r[7] = 2.0f * (qy * qz + qw * qx);
    r[8] = 1.0f - 2.0f * (qx * qx + qy * qy);
}

static void gravity_in_body(const float q[4], float gravity_body[3])
{
    float rotation[9];

    rotation_matrix_from_quaternion(q, rotation);
    gravity_body[0] = GRAVITY_MPS2 * rotation[6];
    gravity_body[1] = GRAVITY_MPS2 * rotation[7];
    gravity_body[2] = GRAVITY_MPS2 * rotation[8];
}

static void raw_to_si(const int16_t raw[6], float accel[3], float gyro[3])
{
    uint8_t axis;

    for (axis = 0; axis < 3; axis++) {
        accel[axis] = raw[axis] * ACCEL_SCALE_MPS2;
        gyro[axis] = raw[axis + 3] * GYRO_SCALE_RADPS;
    }
}

static void apply_accel_calibration(const realtime_traj_t* traj,
                                    const float raw_accel[3],
                                    float accel[3])
{
    float centered[3];
    uint8_t row;

    centered[0] = raw_accel[0] - m_accel_calibration_offset[0];
    centered[1] = raw_accel[1] - m_accel_calibration_offset[1];
    centered[2] = raw_accel[2] - m_accel_calibration_offset[2];

    for (row = 0; row < 3; row++) {
        accel[row] = traj->accel_scale *
          (m_accel_calibration_matrix[3U * row] * centered[0] +
           m_accel_calibration_matrix[3U * row + 1U] * centered[1] +
           m_accel_calibration_matrix[3U * row + 2U] * centered[2]);
    }
}

static uint8_t raw_is_saturated(const int16_t raw[6])
{
    uint8_t axis;

    for (axis = 0; axis < 6; axis++) {
        if ((raw[axis] > 32000) || (raw[axis] < -32000)) {
            return 1U;
        }
    }
    return 0U;
}

static void calculate_angular_rate(const realtime_traj_t* traj,
                                   const float accel[3],
                                   const float gyro[3],
                                   uint8_t stationary,
                                   float angular_rate[3])
{
    float accel_norm = vector_norm(accel);
    float measured[3];
    float predicted[3];
    float error[3];
    float dot;
    float angle;
    float norm_error;
    float gain = 0.0f;
    uint8_t axis;

    for (axis = 0; axis < 3; axis++) {
        angular_rate[axis] = gyro[axis] - traj->gyro_bias[axis];
    }

    if (accel_norm < 0.001f) {
        return;
    }

    measured[0] = accel[0] / accel_norm;
    measured[1] = accel[1] / accel_norm;
    measured[2] = accel[2] / accel_norm;
    predicted[0] = 2.0f * (traj->q[1] * traj->q[3] -
                           traj->q[0] * traj->q[2]);
    predicted[1] = 2.0f * (traj->q[0] * traj->q[1] +
                           traj->q[2] * traj->q[3]);
    predicted[2] = traj->q[0] * traj->q[0] -
                   traj->q[1] * traj->q[1] -
                   traj->q[2] * traj->q[2] +
                   traj->q[3] * traj->q[3];

    dot = clamp_float(predicted[0] * measured[0] +
                      predicted[1] * measured[1] +
                      predicted[2] * measured[2], -1.0f, 1.0f);
    angle = acosf(dot);
    norm_error = fabsf(accel_norm - GRAVITY_MPS2);

    if (stationary) {
        gain = STATIC_ATTITUDE_GAIN;
    } else if ((angle < MOVING_ACCEL_ANGLE_GATE_RAD) &&
               (norm_error < MOVING_ACCEL_NORM_GATE)) {
        gain = MOVING_ATTITUDE_GAIN *
               (1.0f - angle / MOVING_ACCEL_ANGLE_GATE_RAD) *
               (1.0f - norm_error / MOVING_ACCEL_NORM_GATE);
    }

    error[0] = predicted[1] * measured[2] - predicted[2] * measured[1];
    error[1] = predicted[2] * measured[0] - predicted[0] * measured[2];
    error[2] = predicted[0] * measured[1] - predicted[1] * measured[0];
    for (axis = 0; axis < 3; axis++) {
        angular_rate[axis] -= gain * error[axis];
    }
}

static void reset_covariance(realtime_traj_t* traj)
{
    uint8_t axis;

    memset(traj->covariance, 0, sizeof(traj->covariance));
    for (axis = 0; axis < 3; axis++) {
        traj->covariance[MAT_INDEX(STATE_POSITION + axis,
                                   STATE_POSITION + axis)] = 0.0001f;
        traj->covariance[MAT_INDEX(STATE_VELOCITY + axis,
                                   STATE_VELOCITY + axis)] = 0.01f;
        traj->covariance[MAT_INDEX(STATE_ATTITUDE + axis,
                                   STATE_ATTITUDE + axis)] = 0.0025f;
        traj->covariance[MAT_INDEX(STATE_ACCEL_BIAS + axis,
                                   STATE_ACCEL_BIAS + axis)] = 0.04f;
        traj->covariance[MAT_INDEX(STATE_GYRO_BIAS + axis,
                                   STATE_GYRO_BIAS + axis)] = 0.0004f;
    }
}

static void covariance_propagate(realtime_traj_t* traj,
                                 const float rotation[9],
                                 const float accel_body[3],
                                 const float angular_rate[3],
                                 float dt_seconds)
{
    float skew_accel[9] = {
        0.0f, -accel_body[2], accel_body[1],
        accel_body[2], 0.0f, -accel_body[0],
        -accel_body[1], accel_body[0], 0.0f
    };
    float skew_gyro[9] = {
        0.0f, -angular_rate[2], angular_rate[1],
        angular_rate[2], 0.0f, -angular_rate[0],
        -angular_rate[1], angular_rate[0], 0.0f
    };
    float value;
    uint8_t row;
    uint8_t column;
    uint8_t inner;

    memset(traj->transition, 0, sizeof(traj->transition));
    for (row = 0; row < MATRIX_DIM; row++) {
        traj->transition[MAT_INDEX(row, row)] = 1.0f;
    }

    for (row = 0; row < 3; row++) {
        traj->transition[MAT_INDEX(STATE_POSITION + row,
                                   STATE_VELOCITY + row)] = dt_seconds;
        traj->transition[MAT_INDEX(STATE_ATTITUDE + row,
                                   STATE_GYRO_BIAS + row)] = -dt_seconds;
        for (column = 0; column < 3; column++) {
            value = 0.0f;
            for (inner = 0; inner < 3; inner++) {
                value -= rotation[3U * row + inner] *
                         skew_accel[3U * inner + column];
            }
            traj->transition[MAT_INDEX(STATE_VELOCITY + row,
                                       STATE_ATTITUDE + column)] =
              value * dt_seconds;
            traj->transition[MAT_INDEX(STATE_VELOCITY + row,
                                       STATE_ACCEL_BIAS + column)] =
              -rotation[3U * row + column] * dt_seconds;
            traj->transition[MAT_INDEX(STATE_ATTITUDE + row,
                                       STATE_ATTITUDE + column)] +=
              -skew_gyro[3U * row + column] * dt_seconds;
        }
    }

    for (row = 0; row < MATRIX_DIM; row++) {
        for (column = 0; column < MATRIX_DIM; column++) {
            value = 0.0f;
            for (inner = 0; inner < MATRIX_DIM; inner++) {
                value += traj->transition[MAT_INDEX(row, inner)] *
                         traj->covariance[MAT_INDEX(inner, column)];
            }
            traj->matrix_tmp[MAT_INDEX(row, column)] = value;
        }
    }

    for (row = 0; row < MATRIX_DIM; row++) {
        for (column = 0; column < MATRIX_DIM; column++) {
            value = 0.0f;
            for (inner = 0; inner < MATRIX_DIM; inner++) {
                value += traj->matrix_tmp[MAT_INDEX(row, inner)] *
                         traj->transition[MAT_INDEX(column, inner)];
            }
            traj->covariance[MAT_INDEX(row, column)] = value;
        }
    }

    for (row = 0; row < 3; row++) {
        traj->covariance[MAT_INDEX(STATE_POSITION + row,
                                   STATE_POSITION + row)] +=
          0.25f * ACCEL_PROCESS_NOISE * ACCEL_PROCESS_NOISE *
          dt_seconds * dt_seconds * dt_seconds;
        traj->covariance[MAT_INDEX(STATE_VELOCITY + row,
                                   STATE_VELOCITY + row)] +=
          ACCEL_PROCESS_NOISE * ACCEL_PROCESS_NOISE * dt_seconds;
        traj->covariance[MAT_INDEX(STATE_ATTITUDE + row,
                                   STATE_ATTITUDE + row)] +=
          GYRO_PROCESS_NOISE * GYRO_PROCESS_NOISE * dt_seconds;
        traj->covariance[MAT_INDEX(STATE_ACCEL_BIAS + row,
                                   STATE_ACCEL_BIAS + row)] +=
          ACCEL_BIAS_RANDOM_WALK * ACCEL_BIAS_RANDOM_WALK * dt_seconds;
        traj->covariance[MAT_INDEX(STATE_GYRO_BIAS + row,
                                   STATE_GYRO_BIAS + row)] +=
          GYRO_BIAS_RANDOM_WALK * GYRO_BIAS_RANDOM_WALK * dt_seconds;
    }

    for (row = 0; row < MATRIX_DIM; row++) {
        for (column = (uint8_t)(row + 1U); column < MATRIX_DIM; column++) {
            value = 0.5f *
              (traj->covariance[MAT_INDEX(row, column)] +
               traj->covariance[MAT_INDEX(column, row)]);
            traj->covariance[MAT_INDEX(row, column)] = value;
            traj->covariance[MAT_INDEX(column, row)] = value;
        }
    }
}

static void propagate_navigation(realtime_traj_t* traj,
                                 const realtime_traj_sample_t* sample)
{
    float accel_body[3];
    float angular_rate[3];
    float q_half[4];
    float rotation[9];
    float accel_world[3];
    float dt_seconds = sample->dt_seconds;
    uint8_t axis;

    for (axis = 0; axis < 3; axis++) {
        accel_body[axis] = sample->accel[axis] - traj->accel_bias[axis];
    }
    calculate_angular_rate(traj,
                           accel_body,
                           sample->gyro,
                           0U,
                           angular_rate);

    memcpy(q_half, traj->q, sizeof(q_half));
    quaternion_integrate(q_half, angular_rate, 0.5f * dt_seconds);
    rotation_matrix_from_quaternion(q_half, rotation);

    for (axis = 0; axis < 3; axis++) {
        accel_world[axis] =
          rotation[3U * axis] * accel_body[0] +
          rotation[3U * axis + 1U] * accel_body[1] +
          rotation[3U * axis + 2U] * accel_body[2];
    }
    accel_world[2] -= GRAVITY_MPS2;

    for (axis = 0; axis < 3; axis++) {
        traj->position[axis] += traj->velocity[axis] * dt_seconds +
          0.5f * accel_world[axis] * dt_seconds * dt_seconds;
        traj->velocity[axis] += accel_world[axis] * dt_seconds;
    }

    memcpy(traj->q, q_half, sizeof(q_half));
    quaternion_integrate(traj->q, angular_rate, 0.5f * dt_seconds);
    covariance_propagate(traj,
                         rotation,
                         accel_body,
                         angular_rate,
                         dt_seconds);
}

static void zupt_update(realtime_traj_t* traj)
{
    float correction[MATRIX_DIM];
    float covariance_row[MATRIX_DIM];
    float gain[MATRIX_DIM];
    float residual;
    float innovation_variance;
    float measurement_variance =
      ZUPT_VELOCITY_NOISE * ZUPT_VELOCITY_NOISE;
    float attitude_error[3];
    float value;
    uint8_t measurement;
    uint8_t row;
    uint8_t column;

    memset(correction, 0, sizeof(correction));
    for (measurement = 0; measurement < 3; measurement++) {
        uint8_t state_index = (uint8_t)(STATE_VELOCITY + measurement);

        innovation_variance =
          traj->covariance[MAT_INDEX(state_index, state_index)] +
          measurement_variance;
        if (innovation_variance < 0.0000001f) {
            continue;
        }

        residual = -traj->velocity[measurement] - correction[state_index];
        for (column = 0; column < MATRIX_DIM; column++) {
            covariance_row[column] =
              traj->covariance[MAT_INDEX(state_index, column)];
        }
        for (row = 0; row < MATRIX_DIM; row++) {
            /* Published positions are immutable; ZUPT corrects later states only. */
            if (row < STATE_VELOCITY) {
                gain[row] = 0.0f;
            } else {
                gain[row] =
                  traj->covariance[MAT_INDEX(row, state_index)] /
                  innovation_variance;
            }
            correction[row] += gain[row] * residual;
        }
        for (row = 0; row < MATRIX_DIM; row++) {
            for (column = 0; column < MATRIX_DIM; column++) {
                traj->covariance[MAT_INDEX(row, column)] -=
                  gain[row] * covariance_row[column];
            }
        }
    }

    for (row = 0; row < 3; row++) {
        traj->velocity[row] += correction[STATE_VELOCITY + row];
        attitude_error[row] = correction[STATE_ATTITUDE + row];
        traj->accel_bias[row] = clamp_float(
          traj->accel_bias[row] + correction[STATE_ACCEL_BIAS + row],
          -MAX_ACCEL_BIAS,
          MAX_ACCEL_BIAS);
        traj->gyro_bias[row] = clamp_float(
          traj->gyro_bias[row] + correction[STATE_GYRO_BIAS + row],
          -MAX_GYRO_BIAS,
          MAX_GYRO_BIAS);
    }
    quaternion_apply_error(traj->q, attitude_error);
    memset(traj->velocity, 0, sizeof(traj->velocity));

    for (row = 0; row < MATRIX_DIM; row++) {
        for (column = (uint8_t)(row + 1U); column < MATRIX_DIM; column++) {
            value = 0.5f *
              (traj->covariance[MAT_INDEX(row, column)] +
               traj->covariance[MAT_INDEX(column, row)]);
            traj->covariance[MAT_INDEX(row, column)] = value;
            traj->covariance[MAT_INDEX(column, row)] = value;
        }
        if (traj->covariance[MAT_INDEX(row, row)] < 0.000000001f) {
            traj->covariance[MAT_INDEX(row, row)] = 0.000000001f;
        }
    }
}

static void update_stationary_estimates(realtime_traj_t* traj,
                                        const realtime_traj_sample_t* sample)
{
    float accel_body[3];
    float angular_rate[3];
    float gravity_body[3];
    float target_bias;
    uint8_t axis;

    for (axis = 0; axis < 3; axis++) {
        accel_body[axis] = sample->accel[axis] - traj->accel_bias[axis];
        traj->gyro_bias[axis] += STATIC_GYRO_BIAS_RATE *
          (sample->gyro[axis] - traj->gyro_bias[axis]);
    }
    calculate_angular_rate(traj,
                           accel_body,
                           sample->gyro,
                           1U,
                           angular_rate);
    quaternion_integrate(traj->q, angular_rate, sample->dt_seconds);

    gravity_in_body(traj->q, gravity_body);
    for (axis = 0; axis < 3; axis++) {
        target_bias = sample->accel[axis] - gravity_body[axis];
        traj->accel_bias[axis] = clamp_float(
          traj->accel_bias[axis] + STATIC_ACCEL_BIAS_RATE *
          (target_bias - traj->accel_bias[axis]),
          -MAX_ACCEL_BIAS,
          MAX_ACCEL_BIAS);
    }
}

static uint8_t calibration_sample_is_stationary(const float accel[3],
                                                const float gyro[3])
{
    return (fabsf(vector_norm(accel) - GRAVITY_MPS2) < 0.6f) &&
           (vector_norm(gyro) < 0.10f);
}

static void process_calibration(realtime_traj_t* traj,
                                const float accel[3],
                                const float gyro[3])
{
    float average_accel[3];
    uint8_t axis;

    if (!calibration_sample_is_stationary(accel, gyro)) {
        traj->calibration_count = 0U;
        memset(traj->calibration_accel_sum, 0,
               sizeof(traj->calibration_accel_sum));
        memset(traj->calibration_gyro_sum, 0,
               sizeof(traj->calibration_gyro_sum));
        return;
    }

    for (axis = 0; axis < 3; axis++) {
        traj->calibration_accel_sum[axis] += accel[axis];
        traj->calibration_gyro_sum[axis] += gyro[axis];
    }
    traj->calibration_count++;
    if (traj->calibration_count < CALIBRATION_SAMPLES) {
        return;
    }

    for (axis = 0; axis < 3; axis++) {
        average_accel[axis] =
          traj->calibration_accel_sum[axis] / CALIBRATION_SAMPLES;
        traj->gyro_bias[axis] =
          traj->calibration_gyro_sum[axis] / CALIBRATION_SAMPLES;
    }
    /* Six-face scale is fixed; startup only establishes attitude and gyro bias. */
    traj->accel_scale = 1.0f;
    quaternion_from_gravity(average_accel, traj->q);
    traj->state = REALTIME_TRAJ_STATIONARY;
    traj->lag_head = 0U;
    traj->lag_count = 0U;
}

static const realtime_traj_sample_t* push_lag_sample(
  realtime_traj_t* traj,
  const realtime_traj_sample_t* sample)
{
    uint8_t write_index;
    uint8_t center_index;

    if (traj->lag_count == REALTIME_TRAJ_LAG_SAMPLES) {
        traj->lag_head =
          (uint8_t)((traj->lag_head + 1U) % REALTIME_TRAJ_LAG_SAMPLES);
        traj->lag_count--;
    }

    write_index = (uint8_t)((traj->lag_head + traj->lag_count) %
                            REALTIME_TRAJ_LAG_SAMPLES);
    traj->lag_samples[write_index] = *sample;
    traj->lag_count++;
    if (traj->lag_count < REALTIME_TRAJ_LAG_SAMPLES) {
        return NULL;
    }

    center_index = (uint8_t)((traj->lag_head +
                              REALTIME_TRAJ_LAG_SAMPLES / 2U) %
                             REALTIME_TRAJ_LAG_SAMPLES);
    return &traj->lag_samples[center_index];
}

static uint8_t stationary_window_detected(const realtime_traj_t* traj)
{
    float mean_accel[3] = { 0.0f, 0.0f, 0.0f };
    float mean_gyro[3] = { 0.0f, 0.0f, 0.0f };
    float corrected_accel[3];
    float corrected_gyro[3];
    float statistic = 0.0f;
    uint8_t sample_index;
    uint8_t index;
    uint8_t axis;

    for (sample_index = 0; sample_index < REALTIME_TRAJ_LAG_SAMPLES;
         sample_index++) {
        index = (uint8_t)((traj->lag_head + sample_index) %
                          REALTIME_TRAJ_LAG_SAMPLES);
        for (axis = 0; axis < 3; axis++) {
            mean_accel[axis] +=
              traj->lag_samples[index].accel[axis] - traj->accel_bias[axis];
            mean_gyro[axis] +=
              traj->lag_samples[index].gyro[axis] - traj->gyro_bias[axis];
        }
    }
    for (axis = 0; axis < 3; axis++) {
        mean_accel[axis] /= (float)REALTIME_TRAJ_LAG_SAMPLES;
        mean_gyro[axis] /= (float)REALTIME_TRAJ_LAG_SAMPLES;
    }

    if ((fabsf(vector_norm(mean_accel) - GRAVITY_MPS2) >
         STATIC_ACCEL_NORM_TOLERANCE) ||
        (vector_norm(mean_gyro) > STATIC_GYRO_MEAN_LIMIT)) {
        return 0U;
    }

    for (sample_index = 0; sample_index < REALTIME_TRAJ_LAG_SAMPLES;
         sample_index++) {
        index = (uint8_t)((traj->lag_head + sample_index) %
                          REALTIME_TRAJ_LAG_SAMPLES);
        for (axis = 0; axis < 3; axis++) {
            corrected_accel[axis] =
              traj->lag_samples[index].accel[axis] -
              traj->accel_bias[axis] - mean_accel[axis];
            corrected_gyro[axis] =
              traj->lag_samples[index].gyro[axis] -
              traj->gyro_bias[axis] - mean_gyro[axis];
            statistic +=
              corrected_accel[axis] * corrected_accel[axis] /
              (SHOE_ACCEL_NOISE * SHOE_ACCEL_NOISE) +
              corrected_gyro[axis] * corrected_gyro[axis] /
              (SHOE_GYRO_NOISE * SHOE_GYRO_NOISE);
        }
    }

    statistic /= (float)REALTIME_TRAJ_LAG_SAMPLES;
    return statistic < SHOE_STATIONARY_THRESHOLD;
}

static uint8_t navigation_state_is_valid(const realtime_traj_t* traj)
{
    uint8_t axis;

    for (axis = 0; axis < 3; axis++) {
        if ((traj->position[axis] != traj->position[axis]) ||
            (traj->velocity[axis] != traj->velocity[axis]) ||
            (fabsf(traj->position[axis]) > MAX_NAVIGATION_POSITION_M) ||
            (fabsf(traj->velocity[axis]) > MAX_NAVIGATION_SPEED_MPS)) {
            return 0U;
        }
    }
    for (axis = 0; axis < 4; axis++) {
        if ((traj->q[axis] != traj->q[axis]) ||
            (fabsf(traj->q[axis]) > 1.1f)) {
            return 0U;
        }
    }
    return 1U;
}

static void reset_for_recalibration(realtime_traj_t* traj)
{
    memset(traj->position, 0, sizeof(traj->position));
    memset(traj->velocity, 0, sizeof(traj->velocity));
    memset(traj->gyro_bias, 0, sizeof(traj->gyro_bias));
    memset(traj->accel_bias, 0, sizeof(traj->accel_bias));
    memset(traj->calibration_accel_sum, 0,
           sizeof(traj->calibration_accel_sum));
    memset(traj->calibration_gyro_sum, 0,
           sizeof(traj->calibration_gyro_sum));
    traj->q[0] = 1.0f;
    traj->q[1] = 0.0f;
    traj->q[2] = 0.0f;
    traj->q[3] = 0.0f;
    traj->accel_scale = 1.0f;
    traj->calibration_count = 0U;
    traj->lag_head = 0U;
    traj->lag_count = 0U;
    traj->motion_confirm_count = 0U;
    traj->stationary_confirm_count = 0U;
    traj->invalid_sample_count = 0U;
    traj->output_divider = 0U;
    traj->quality_flags = TRAJECTORY_FLAG_UNRELIABLE;
    traj->state = REALTIME_TRAJ_CALIBRATING;
    reset_covariance(traj);
}

static void start_segment(realtime_traj_t* traj,
                          const realtime_traj_sample_t* sample)
{
    memset(traj->position, 0, sizeof(traj->position));
    memset(traj->velocity, 0, sizeof(traj->velocity));
    quaternion_zero_yaw(traj->q);
    reset_covariance(traj);
    traj->stationary_confirm_count = 0U;
    traj->output_divider = 0U;
    traj->quality_flags = sample->quality_flags;
    traj->state = REALTIME_TRAJ_MOVING;
    propagate_navigation(traj, sample);
    traj->output_divider = 1U;
}

void realtime_traj_init(realtime_traj_t* traj)
{
    memset(traj, 0, sizeof(*traj));
    traj->q[0] = 1.0f;
    traj->accel_scale = 1.0f;
    traj->state = REALTIME_TRAJ_CALIBRATING;
    reset_covariance(traj);
}

uint8_t realtime_traj_take_quality_flags(realtime_traj_t* traj)
{
    uint8_t flags = traj->quality_flags;

    traj->quality_flags = 0U;
    return flags;
}

realtime_traj_event_t realtime_traj_process(realtime_traj_t* traj,
                                            const int16_t raw[6],
                                            float dt_seconds,
                                            uint16_t input_tick_10ms,
                                            float position_m[3],
                                            uint16_t* output_tick_10ms)
{
    realtime_traj_sample_t sample;
    const realtime_traj_sample_t* delayed_sample;
    float raw_accel[3];
    float raw_gyro[3];
    float calibrated_accel[3];
    uint8_t stationary;

    raw_to_si(raw, raw_accel, raw_gyro);
    if (raw_is_saturated(raw)) {
        traj->quality_flags |= TRAJECTORY_FLAG_UNRELIABLE;
        traj->lag_head = 0U;
        traj->lag_count = 0U;
        traj->invalid_sample_count++;
        if ((traj->state == REALTIME_TRAJ_MOVING) &&
            (traj->invalid_sample_count >= MAX_INVALID_SAMPLES)) {
            *output_tick_10ms = input_tick_10ms;
            reset_for_recalibration(traj);
            copy_position(traj, position_m);
            return REALTIME_TRAJ_ABORT;
        }
        copy_position(traj, position_m);
        return REALTIME_TRAJ_NO_OUTPUT;
    }
    traj->invalid_sample_count = 0U;

    if (traj->state == REALTIME_TRAJ_CALIBRATING) {
        apply_accel_calibration(traj, raw_accel, calibrated_accel);
        process_calibration(traj, calibrated_accel, raw_gyro);
        copy_position(traj, position_m);
        return REALTIME_TRAJ_NO_OUTPUT;
    }

    sample.quality_flags = 0U;
    if ((dt_seconds < 0.005f) || (dt_seconds > 0.015f)) {
        sample.quality_flags |= TRAJECTORY_FLAG_SAMPLE_GAP;
    }
    sample.dt_seconds = clamp_float(dt_seconds, 0.005f, 0.03f);
    sample.tick_10ms = input_tick_10ms;
    apply_accel_calibration(traj, raw_accel, sample.accel);
    memcpy(sample.gyro, raw_gyro, sizeof(sample.gyro));

    delayed_sample = push_lag_sample(traj, &sample);
    if (delayed_sample == NULL) {
        copy_position(traj, position_m);
        return REALTIME_TRAJ_NO_OUTPUT;
    }

    stationary = stationary_window_detected(traj);
    *output_tick_10ms = delayed_sample->tick_10ms;

    if (traj->state == REALTIME_TRAJ_STATIONARY) {
        if (stationary) {
            traj->motion_confirm_count = 0U;
            update_stationary_estimates(traj, delayed_sample);
            copy_position(traj, position_m);
            return REALTIME_TRAJ_NO_OUTPUT;
        }

        traj->motion_confirm_count++;
        if (traj->motion_confirm_count < MOTION_CONFIRM_SAMPLES) {
            copy_position(traj, position_m);
            return REALTIME_TRAJ_NO_OUTPUT;
        }

        traj->motion_confirm_count = 0U;
        start_segment(traj, delayed_sample);
        position_m[0] = 0.0f;
        position_m[1] = 0.0f;
        position_m[2] = 0.0f;
        return REALTIME_TRAJ_ORIGIN;
    }

    traj->quality_flags |= delayed_sample->quality_flags;
    propagate_navigation(traj, delayed_sample);
    traj->output_divider++;

    if (!navigation_state_is_valid(traj)) {
        reset_for_recalibration(traj);
        copy_position(traj, position_m);
        return REALTIME_TRAJ_ABORT;
    }

    if (stationary) {
        traj->stationary_confirm_count++;
        if (traj->stationary_confirm_count >= STOP_CONFIRM_SAMPLES) {
            update_stationary_estimates(traj, delayed_sample);
            zupt_update(traj);
            traj->state = REALTIME_TRAJ_STATIONARY;
            traj->motion_confirm_count = 0U;
            copy_position(traj, position_m);
            return REALTIME_TRAJ_STOP;
        }
    } else {
        traj->stationary_confirm_count = 0U;
    }

    if (traj->output_divider >=
        (REALTIME_TRAJ_SAMPLE_HZ / REALTIME_TRAJ_OUTPUT_HZ)) {
        traj->output_divider = 0U;
        copy_position(traj, position_m);
        return REALTIME_TRAJ_POINT;
    }

    copy_position(traj, position_m);
    return REALTIME_TRAJ_NO_OUTPUT;
}

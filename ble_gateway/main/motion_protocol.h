#ifndef MOTION_PROTOCOL_H
#define MOTION_PROTOCOL_H

#include <stdbool.h>
#include <stdint.h>

#define MOTION_PACKET_SIZE          100U
#define MOTION_PROTOCOL_VERSION     1U
#define MOTION_PACKET_TYPE_DATA     0x10U
#define MOTION_SAMPLE_COUNT         4U
#define MOTION_IMU_FORMAT_RAW_INT16 1U

typedef struct {
    uint16_t tick_offset_32k;
    int16_t accel[3];
    int16_t gyro[3];
} motion_imu_sample_t;

typedef struct {
    uint8_t device_id;
    uint8_t flags;
    uint32_t session_id;
    uint32_t first_sample_sequence;
    uint64_t first_sample_tick_32k;
    uint16_t packet_sequence;
    motion_imu_sample_t samples[MOTION_SAMPLE_COUNT];
    uint32_t coordinate_source_sequence;
    int32_t position_mm[3];
    uint16_t coordinate_flags;
} motion_packet_t;

static inline uint16_t motion_get_le16(const uint8_t *src)
{
    return (uint16_t)src[0] | ((uint16_t)src[1] << 8);
}

static inline uint32_t motion_get_le32(const uint8_t *src)
{
    return (uint32_t)src[0] |
           ((uint32_t)src[1] << 8) |
           ((uint32_t)src[2] << 16) |
           ((uint32_t)src[3] << 24);
}

static inline uint64_t motion_get_le64(const uint8_t *src)
{
    uint64_t value = 0;
    int index;

    for (index = 7; index >= 0; index--) {
        value = (value << 8) | src[index];
    }
    return value;
}

static inline bool motion_packet_decode(const uint8_t *data,
                                        uint16_t length,
                                        motion_packet_t *packet)
{
    uint8_t sample_index;
    uint8_t axis;
    const uint8_t *sample;

    if (data == NULL || packet == NULL || length != MOTION_PACKET_SIZE ||
        data[0] != MOTION_PROTOCOL_VERSION ||
        data[1] != MOTION_PACKET_TYPE_DATA ||
        data[22] != MOTION_SAMPLE_COUNT ||
        data[23] != MOTION_IMU_FORMAT_RAW_INT16) {
        return false;
    }

    packet->device_id = data[2];
    packet->flags = data[3];
    packet->session_id = motion_get_le32(&data[4]);
    packet->first_sample_sequence = motion_get_le32(&data[8]);
    packet->first_sample_tick_32k = motion_get_le64(&data[12]);
    packet->packet_sequence = motion_get_le16(&data[20]);

    for (sample_index = 0; sample_index < MOTION_SAMPLE_COUNT;
         sample_index++) {
        sample = &data[24U + 14U * sample_index];
        packet->samples[sample_index].tick_offset_32k =
            motion_get_le16(sample);
        for (axis = 0; axis < 3U; axis++) {
            packet->samples[sample_index].accel[axis] =
                (int16_t)motion_get_le16(&sample[2U + 2U * axis]);
            packet->samples[sample_index].gyro[axis] =
                (int16_t)motion_get_le16(&sample[8U + 2U * axis]);
        }
    }

    packet->coordinate_source_sequence = motion_get_le32(&data[80]);
    for (axis = 0; axis < 3U; axis++) {
        packet->position_mm[axis] =
            (int32_t)motion_get_le32(&data[84U + 4U * axis]);
    }
    packet->coordinate_flags = motion_get_le16(&data[96]);
    return true;
}

#endif

#ifndef TRAJECTORY_PROTOCOL_H
#define TRAJECTORY_PROTOCOL_H

#include <stdbool.h>
#include <stdint.h>

#define TRAJECTORY_FRAME_SIZE             20U
#define TRAJECTORY_FRAME_START            0xA5U
#define TRAJECTORY_FRAME_END              0x5AU

#define TRAJECTORY_FLAG_CALIBRATING       0x01U
#define TRAJECTORY_FLAG_STATIONARY        0x02U
#define TRAJECTORY_FLAG_MOVING            0x04U
#define TRAJECTORY_FLAG_ORIGIN            0x08U
#define TRAJECTORY_FLAG_STOP              0x10U
#define TRAJECTORY_FLAG_QUEUE_DROP        0x20U
#define TRAJECTORY_FLAG_SAMPLE_GAP        0x40U
#define TRAJECTORY_FLAG_UNRELIABLE        0x80U

typedef struct {
    uint8_t device_id;
    uint16_t sequence;
    int32_t position_mm[3];
    uint16_t sample_tick_10ms;
    uint8_t flags;
} trajectory_frame_t;

static inline uint16_t trajectory_get_le16(const uint8_t *src)
{
    return (uint16_t)src[0] | ((uint16_t)src[1] << 8);
}

static inline uint32_t trajectory_get_le32(const uint8_t *src)
{
    return (uint32_t)src[0] |
           ((uint32_t)src[1] << 8) |
           ((uint32_t)src[2] << 16) |
           ((uint32_t)src[3] << 24);
}

static inline bool trajectory_frame_decode(const uint8_t *data,
                                           uint16_t length,
                                           trajectory_frame_t *frame)
{
    uint8_t axis;

    if (length != TRAJECTORY_FRAME_SIZE ||
        data[0] != TRAJECTORY_FRAME_START ||
        data[19] != TRAJECTORY_FRAME_END) {
        return false;
    }

    frame->device_id = data[1];
    frame->sequence = trajectory_get_le16(&data[2]);
    for (axis = 0; axis < 3; axis++) {
        frame->position_mm[axis] =
            (int32_t)trajectory_get_le32(&data[4U + 4U * axis]);
    }
    frame->sample_tick_10ms = trajectory_get_le16(&data[16]);
    frame->flags = data[18];
    return true;
}

#endif

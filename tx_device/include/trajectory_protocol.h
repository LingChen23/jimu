#ifndef TRAJECTORY_PROTOCOL_H
#define TRAJECTORY_PROTOCOL_H

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

#define TRAJECTORY_OFFSET_FLAGS           18U

static void trajectory_put_le16(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
}

static void trajectory_put_le32(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
    dst[2] = (uint8_t)(value >> 16);
    dst[3] = (uint8_t)(value >> 24);
}

static void trajectory_frame_encode(uint8_t frame[TRAJECTORY_FRAME_SIZE],
                                    uint8_t device_id,
                                    uint16_t sequence,
                                    const int32_t position_mm[3],
                                    uint16_t sample_tick_10ms,
                                    uint8_t flags)
{
    uint8_t axis;

    frame[0] = TRAJECTORY_FRAME_START;
    frame[1] = device_id;
    trajectory_put_le16(&frame[2], sequence);
    for (axis = 0; axis < 3; axis++) {
        trajectory_put_le32(&frame[4U + 4U * axis],
                            (uint32_t)position_mm[axis]);
    }
    trajectory_put_le16(&frame[16], sample_tick_10ms);
    frame[TRAJECTORY_OFFSET_FLAGS] = flags;
    frame[19] = TRAJECTORY_FRAME_END;
}

#endif

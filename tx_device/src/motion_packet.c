#include "motion_packet.h"

#include <string.h>

static void put_le16(uint8_t* dst, uint16_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
}

static void put_le32(uint8_t* dst, uint32_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
    dst[2] = (uint8_t)(value >> 16);
    dst[3] = (uint8_t)(value >> 24);
}

static int16_t saturate_i16(int32_t value)
{
    if (value > 32767) {
        return 32767;
    }
    if (value < -32768) {
        return -32768;
    }
    return (int16_t)value;
}

void motion_packet_encode(uint8_t packet[MOTION_PACKET_SIZE],
                          const motion_packet_fields_t* fields)
{
    uint8_t sample_index;
    uint8_t axis;
    uint8_t* sample;
    uint32_t last_sample_sequence;
    uint32_t coordinate_age;

    memset(packet, 0, MOTION_PACKET_SIZE);
    packet[0] = MOTION_PROTOCOL_VERSION;
    packet[1] = MOTION_PACKET_TYPE_DATA;
    packet[2] = fields->device_id;
    packet[3] = fields->flags;
    put_le32(&packet[4], fields->session_id);
    put_le32(&packet[8], fields->first_sample_sequence);
    put_le32(&packet[12], (uint32_t)fields->first_sample_tick_32k);
    put_le16(&packet[16], fields->packet_sequence);
    packet[18] = MOTION_SAMPLE_COUNT;
    packet[19] = MOTION_IMU_FORMAT_RAW_INT16;

    for (sample_index = 0; sample_index < MOTION_SAMPLE_COUNT;
         sample_index++) {
        sample = &packet[20U + 12U * sample_index];
        for (axis = 0; axis < 6U; axis++) {
            put_le16(&sample[2U * axis],
                     (uint16_t)fields->samples[sample_index].axes[axis]);
        }
    }

    last_sample_sequence = fields->first_sample_sequence +
                           (MOTION_SAMPLE_COUNT - 1U);
    coordinate_age = last_sample_sequence -
                     fields->coordinate_source_sequence;
    if (coordinate_age > 255U) {
        coordinate_age = 255U;
    }
    for (axis = 0; axis < 3U; axis++) {
        put_le16(&packet[92U + 2U * axis],
                 (uint16_t)saturate_i16(fields->position_mm[axis]));
    }
    packet[98] = (uint8_t)coordinate_age;
    packet[99] = (uint8_t)fields->coordinate_flags;
}

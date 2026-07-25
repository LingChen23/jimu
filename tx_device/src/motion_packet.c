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

static void put_le64(uint8_t* dst, uint64_t value)
{
    uint8_t index;

    for (index = 0; index < 8U; index++) {
        dst[index] = (uint8_t)(value >> (8U * index));
    }
}

void motion_packet_encode(uint8_t packet[MOTION_PACKET_SIZE],
                          const motion_packet_fields_t* fields)
{
    uint8_t sample_index;
    uint8_t axis;
    uint8_t* sample;

    memset(packet, 0, MOTION_PACKET_SIZE);
    packet[0] = MOTION_PROTOCOL_VERSION;
    packet[1] = MOTION_PACKET_TYPE_DATA;
    packet[2] = fields->device_id;
    packet[3] = fields->flags;
    put_le32(&packet[4], fields->session_id);
    put_le32(&packet[8], fields->first_sample_sequence);
    put_le64(&packet[12], fields->first_sample_tick_32k);
    put_le16(&packet[20], fields->packet_sequence);
    packet[22] = MOTION_SAMPLE_COUNT;
    packet[23] = MOTION_IMU_FORMAT_RAW_INT16;

    for (sample_index = 0; sample_index < MOTION_SAMPLE_COUNT;
         sample_index++) {
        sample = &packet[24U + 14U * sample_index];
        put_le16(sample, fields->samples[sample_index].tick_offset_32k);
        for (axis = 0; axis < 6U; axis++) {
            put_le16(&sample[2U + 2U * axis],
                     (uint16_t)fields->samples[sample_index].axes[axis]);
        }
    }

    put_le32(&packet[80], fields->coordinate_source_sequence);
    for (axis = 0; axis < 3U; axis++) {
        put_le32(&packet[84U + 4U * axis],
                 (uint32_t)fields->position_mm[axis]);
    }
    put_le16(&packet[96], fields->coordinate_flags);
    put_le16(&packet[98], 0U);
}

#ifndef MOTION_PACKET_H
#define MOTION_PACKET_H

#include <stdint.h>

#define MOTION_PACKET_SIZE              100U
#define MOTION_PROTOCOL_VERSION         2U
#define MOTION_PACKET_TYPE_DATA         0x10U
#define MOTION_SAMPLE_COUNT             6U
#define MOTION_IMU_FORMAT_RAW_INT16     1U

#define MOTION_PACKET_FLAG_CALIBRATED       0x01U
#define MOTION_PACKET_FLAG_SAMPLE_GAP       0x02U
#define MOTION_PACKET_FLAG_ACCEL_SATURATED  0x04U
#define MOTION_PACKET_FLAG_GYRO_SATURATED   0x08U
#define MOTION_PACKET_FLAG_SENSOR_ERROR     0x10U
#define MOTION_PACKET_FLAG_QUEUE_DROP       0x20U
#define MOTION_PACKET_FLAG_COORD_VALID      0x40U
#define MOTION_PACKET_FLAG_UNRELIABLE       0x80U

#define MOTION_COORD_FLAG_STATIONARY    0x0001U
#define MOTION_COORD_FLAG_MOVING        0x0002U
#define MOTION_COORD_FLAG_ORIGIN        0x0004U
#define MOTION_COORD_FLAG_STOP          0x0008U
#define MOTION_COORD_FLAG_HELD          0x0010U
#define MOTION_COORD_FLAG_UNRELIABLE    0x0020U
#define MOTION_COORD_FLAG_CALIBRATING   0x0040U
#define MOTION_COORD_FLAG_ABORTED       0x0080U

#define MOTION_PACKET_FLAGS_OFFSET      3U

typedef struct
{
    uint16_t tick_offset_32k;
    int16_t axes[6];
} motion_imu_sample_t;

typedef struct
{
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
} motion_packet_fields_t;

void motion_packet_encode(uint8_t packet[MOTION_PACKET_SIZE],
                          const motion_packet_fields_t* fields);

#endif

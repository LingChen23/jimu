#ifndef __ICM_20689_H__
#define __ICM_20689_H__

//#include "stdbool.h"
#include "stdint.h"
//#include "string.h"

#ifdef __cplusplus
extern "C" {
#endif

#define ICM_20689_ID        0X98

#define ICM_20689_FIFO_SIZE 4096

#define SELF_TEST_X_GYRO    0x00
#define SELF_TEST_Y_GYRO    0x01 
#define SELF_TEST_Z_GYRO    0x02

#define SELF_TEST_X_ACCEL   0x0D	 
#define SELF_TEST_Y_ACCEL   0x0E	 
#define SELF_TEST_Z_ACCEL   0x0F	

#define XG_OFFS_USRH        0x13	
#define XG_OFFS_USRL        0x14
#define YG_OFFS_USRH        0x15
#define YG_OFFS_USRL        0x16
#define ZG_OFFS_USRH        0x17
#define ZG_OFFS_USRL        0x18

#define SMPLRT_DIV          0x19

#define ICM_CONFIG              0x1A
#define GYRO_CONFIG         0x1B
#define ACCEL_CONFIG        0x1C
#define ACCEL_CONFIG_2      0x1D

#define LP_MODE_CFG         0x1E

#define ACCEL_WOM_X_THR     0x20
#define ACCEL_WOM_Y_THR     0x21
#define ACCEL_WOM_Z_THR     0x22

#define FIFO_EN	            0X23

#define FSYNC_INT           0X36

#define INT_PIN_CFG	        0X37
#define INT_ENABLE          0X38
#define INT_STATUS          0X3A

#define ACCEL_XOUT_H        0x3B
#define ACCEL_XOUT_L        0x3C
#define ACCEL_YOUT_H        0x3D
#define ACCEL_YOUT_L        0x3E
#define ACCEL_ZOUT_H        0x3F
#define ACCEL_ZOUT_L        0x40
#define TEMP_OUT_H          0x41
#define TEMP_OUT_L          0x42
#define GYRO_XOUT_H         0x43
#define GYRO_XOUT_L         0x44
#define GYRO_YOUT_H         0x45
#define GYRO_YOUT_L         0x46
#define GYRO_ZOUT_H         0x47
#define GYRO_ZOUT_L         0x48

#define SIGNAL_PATH_RESET   0x68

#define ACCEL_INTEL_CTRL    0x69

#define USER_CTRL           0x6A

#define PWR_MGMT_1          0x6B
#define PWR_MGMT_2          0x6C

#define FIFO_COUNTH         0x72
#define FIFO_COUNTL         0x73
#define FIFO_R_W            0x74

#define WHO_AM_I            0x75

#define XA_OFFS_H           0x77
#define XA_OFFS_L           0x78
#define YA_OFFS_H           0x7a
#define YA_OFFS_L           0x7b
#define ZA_OFFS_H           0x7d
#define ZA_OFFS_L           0x7e

#define ICM_20689_CTRL_SET_AXIS_MAP    (RT_SENSOR_CTRL_USER_CMD_START + 1)
#define ICM_20689_CTRL_SET_AXIS_NEGATE (RT_SENSOR_CTRL_USER_CMD_START + 2)
#define ICM_20689_CTRL_SET_AXIS_CALI   (RT_SENSOR_CTRL_USER_CMD_START + 3)
#define ICM_20689_CTRL_SET_WORK_MODE   (RT_SENSOR_CTRL_USER_CMD_START + 4)
#define ICM_20689_CTRL_SET_ACCE_RANGE  (RT_SENSOR_CTRL_USER_CMD_START + 5)
#define ICM_20689_CTRL_SET_GYRO_RANGE  (RT_SENSOR_CTRL_USER_CMD_START + 6)

enum mpu_accel_fs {	// In the ACCEL_CONFIG (0x1C) register, the full scale select  bits are :
    MPU_FS_2G = 0,	// 00 = 2G
    MPU_FS_4G,		// 01 = 4
	  MPU_FS_8G,		// 10 = 8
    MPU_FS_16G,		// 11 = 16
    NUM_MPU_AFS
};

enum mpu_accel_ois_fs {	// In the ACCEL_CONFIG (0x1C) register, the accel ois full scale select bits are :
    MPU_OIS_FS_2G = 0,	// 00 = 2G
    MPU_OIS_FS_4G,		// 01 = 4
	  MPU_OIS_FS_8G,		// 10 = 8
    MPU_OIS_FS_1G,		// 11 = 1
    NUM_MPU_OIS_AFS
};

enum mpu_accel_bw {		// In the ACCEL_CONFIG2 (0x1D) register, the BW setting bits are :
	MPU_ABW_218 = 1,	// 001 = 218 Hz
	MPU_ABW_99,			// 010 = 99 Hz
	MPU_ABW_45,			// 011 = 45 Hz
	MPU_ABW_21,			// 100 = 21 Hz
	MPU_ABW_10,			// 101 = 10 Hz
	MPU_ABW_5,			// 110 = 5 Hz
	MPU_ABW_420,		// 111 = 420 Hz
	NUM_MPU_ABW
};

enum mpu_gyro_fs {		// In the GYRO_CONFIG register, the fS_SEL bits are :
	MPU_FS_250dps = 0,	// 00 = 250
	MPU_FS_500dps,		// 01 = 500
	MPU_FS_1000dps,		// 10 = 1000
	MPU_FS_2000dps,		// 11 = 2000
#if (MEMS_CHIP == HW_ICM20690)
	MPU_FS_31dps = 0x5,	// 101 = 31.25
	MPU_FS_62dps,		// 110 = 62.5
	MPU_FS_125dps,		// 111 = 125
#endif
	NUM_MPU_GFS
};

enum mpu_gyro_bw {   // In the ICM_CONFIG register, the  BW setting bits are :
	MPU_GBW_176 = 1, // 001 = 176 Hz
	MPU_GBW_92,		 // 010 = 92 Hz
	MPU_GBW_41,		 // 011 = 41 Hz
	MPU_GBW_20,		 // 100 = 20 Hz
	MPU_GBW_10,		 // 101 = 10 Hz
	MPU_GBW_5,		 // 110 = 5 Hz
    NUM_MPU_GBW
};

typedef enum
{
    ICM_20689_WORK_MODE_POLLING,
    ICM_20689_WORK_MODE_INT,
    ICM_20689_WORK_MODE_FIFO,
    ICM_20689_WORK_MODE_WAKEUP
} icm_20689_work_mode_t;

typedef struct
{
    int soft_mutex;
    int power_enabled;

    uint8_t fifo_buf[ICM_20689_FIFO_SIZE];

    uint16_t state;

    float sample_freq;
    float acce_bw;
    float gyro_bw;

    uint32_t acce_range;
    uint32_t gyro_range;

    /*
     * By default use, x is axis 0, y is axis 1, z is axis 2; these can be
     * changed to account for sensor orientation within the host device.
     */
    uint8_t x_map;
    uint8_t y_map;
    uint8_t z_map;

    /*
     * Each axis can be negated to account for sensor orientation within
     * the host device.
     */
    uint8_t x_negate;
    uint8_t y_negate;
    uint8_t z_negate;

    int16_t accel_cali[3];
    int16_t gyro_cali[3];
} icm_20689_dev_data_t;

#endif

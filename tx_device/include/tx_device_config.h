#ifndef TX_DEVICE_CONFIG_H
#define TX_DEVICE_CONFIG_H

/*
 * Set this to a unique value before building firmware for each physical TX.
 * Valid values are 1..255. Value 0 is reserved and must not be used.
 */
#ifndef TX_DEVICE_ID
#define TX_DEVICE_ID 4U
#endif

#if (TX_DEVICE_ID < 1U) || (TX_DEVICE_ID > 255U)
#error "TX_DEVICE_ID must be in the range 1..255"
#endif

#endif

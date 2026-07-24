#ifndef DEVICE_INFO_H_
#define DEVICE_INFO_H_

#include "stdint.h"

void device_info_init(void);
void device_info_read(uint32_t *sn);

#endif

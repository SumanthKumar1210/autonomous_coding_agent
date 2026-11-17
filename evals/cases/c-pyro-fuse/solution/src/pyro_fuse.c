/**
 * @file pyro_fuse.c
 * @brief Pyrotechnic fuse driver implementation.
 */
#include "pyro_fuse.h"

#include "hal.h"

static bool s_fired;

void pyro_fuse_init(void)
{
    s_fired = false;
}

bms_status_t pyro_fuse_fire(void)
{
    if (s_fired) {
        return BMS_ERR_STATE;
    }
    s_fired = true; /* before the HAL call: never fire twice, even after a failure */
    if (hal_fire_pyro_fuse() != BMS_OK) {
        return BMS_ERR_HW;
    }
    return BMS_OK;
}

bool pyro_fuse_is_fired(void)
{
    return s_fired;
}

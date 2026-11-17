/**
 * @file contactor.c
 * @brief Main contactor control implementation.
 */
#include "contactor.h"

#include "fault.h"
#include "hal.h"

static bool s_closed;

void contactor_init(void)
{
    s_closed = false;
}

bms_status_t contactor_close(void)
{
    if (fault_any_active()) {
        return BMS_ERR_STATE;
    }
    if (hal_set_contactor(true) != BMS_OK) {
        return BMS_ERR_HW;
    }
    s_closed = true;
    return BMS_OK;
}

bms_status_t contactor_open(void)
{
    if (hal_set_contactor(false) != BMS_OK) {
        return BMS_ERR_HW;
    }
    s_closed = false;
    return BMS_OK;
}

bool contactor_is_closed(void)
{
    return s_closed;
}

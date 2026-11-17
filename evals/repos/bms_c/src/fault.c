/**
 * @file fault.c
 * @brief Fault manager implementation.
 */
#include "fault.h"

static bool s_active[FAULT_COUNT];

void fault_init(void)
{
    for (uint8_t i = 0U; i < (uint8_t)FAULT_COUNT; i++) {
        s_active[i] = false;
    }
}

bms_status_t fault_set(fault_code_t code)
{
    if ((uint32_t)code >= (uint32_t)FAULT_COUNT) {
        return BMS_ERR_PARAM;
    }
    s_active[code] = true;
    return BMS_OK;
}

bms_status_t fault_clear(fault_code_t code)
{
    if ((uint32_t)code >= (uint32_t)FAULT_COUNT) {
        return BMS_ERR_PARAM;
    }
    s_active[code] = false;
    return BMS_OK;
}

bool fault_is_active(fault_code_t code)
{
    if ((uint32_t)code >= (uint32_t)FAULT_COUNT) {
        return false;
    }
    return s_active[code];
}

bool fault_any_active(void)
{
    for (uint8_t i = 0U; i < (uint8_t)FAULT_COUNT; i++) {
        if (s_active[i]) {
            return true;
        }
    }
    return false;
}

/**
 * @file overcurrent.c
 * @brief Overcurrent protection implementation.
 */
#include "overcurrent.h"

#include "contactor.h"
#include "fault.h"
#include "hal.h"

static bool s_above;          /* debounce running */
static uint32_t s_since_ms;   /* time of the first sample above the limit */
static uint32_t s_last_mag_ma;

/* |ma| without overflow: -(int64_t)INT32_MIN fits in uint32_t. */
static uint32_t magnitude_ma(int32_t ma)
{
    return (ma < 0) ? (uint32_t)(-(int64_t)ma) : (uint32_t)ma;
}

void overcurrent_init(void)
{
    s_above = false;
    s_since_ms = 0U;
    s_last_mag_ma = 0U;
}

bms_status_t overcurrent_update(void)
{
    int32_t ma = 0;

    if (hal_read_current_ma(&ma) != BMS_OK) {
        (void)fault_set(FAULT_SENSOR);
        s_above = false;
        return BMS_ERR_HW;
    }
    const uint32_t now_ms = hal_time_ms();
    s_last_mag_ma = magnitude_ma(ma);

    if (s_last_mag_ma <= (uint32_t)OVERCURRENT_LIMIT_MA) {
        s_above = false;
        return BMS_OK;
    }
    if (!s_above) {
        s_above = true;
        s_since_ms = now_ms;
    }
    /* Unsigned subtraction stays correct across the hal_time_ms() wrap. */
    if ((uint32_t)(now_ms - s_since_ms) >= OVERCURRENT_DEBOUNCE_MS) {
        (void)fault_set(FAULT_OVERCURRENT);
        (void)contactor_open();
    }
    return BMS_OK;
}

bms_status_t overcurrent_reset(void)
{
    if (s_last_mag_ma > (uint32_t)OVERCURRENT_LIMIT_MA) {
        return BMS_ERR_STATE;
    }
    (void)fault_clear(FAULT_OVERCURRENT);
    return BMS_OK;
}

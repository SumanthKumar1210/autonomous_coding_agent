/**
 * @file soc.c
 * @brief Coulomb-counting state of charge.
 *
 * Charge is tracked in mA x ms (microampere-seconds) so that many small time
 * steps do not accumulate rounding drift. The stored charge saturates at
 * 0 and at the capacity, so the estimate recovers as soon as the current
 * reverses.
 */
#include "soc.h"

/** mA x ms per mAh. */
#define UAS_PER_MAH 3600000LL

static int64_t s_capacity_uas;
static int64_t s_charge_uas;

bms_status_t soc_init(uint32_t capacity_mah, uint8_t initial_pct)
{
    if ((capacity_mah == 0U) || (initial_pct > 100U)) {
        return BMS_ERR_PARAM;
    }
    s_capacity_uas = (int64_t)capacity_mah * UAS_PER_MAH;
    s_charge_uas = (s_capacity_uas * (int64_t)initial_pct) / 100LL;
    return BMS_OK;
}

void soc_update(int32_t current_ma, uint32_t dt_ms)
{
    /* |current| * dt < 2^63 and 0 <= charge <= capacity, so neither the product nor the sum can overflow. */
    const int64_t delta = (int64_t)current_ma * (int64_t)dt_ms;

    if (delta > 0) {
        s_charge_uas = (delta >= (s_capacity_uas - s_charge_uas)) ? s_capacity_uas : (s_charge_uas + delta);
    } else {
        s_charge_uas = (-delta >= s_charge_uas) ? 0 : (s_charge_uas + delta);
    }
}

uint8_t soc_get_pct(void)
{
    if (s_capacity_uas == 0LL) {
        return 0U;
    }
    return (uint8_t)((s_charge_uas * 100LL) / s_capacity_uas);
}

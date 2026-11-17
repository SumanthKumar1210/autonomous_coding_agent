/**
 * @file soc.c
 * @brief Coulomb-counting state of charge.
 *
 * Charge is tracked in mA x ms (microampere-seconds) so that many small time
 * steps do not accumulate rounding drift.
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
    s_charge_uas += (int64_t)current_ma * (int64_t)dt_ms;
}

uint8_t soc_get_pct(void)
{
    if (s_capacity_uas == 0LL) {
        return 0U;
    }
    return (uint8_t)((s_charge_uas * 100LL) / s_capacity_uas);
}

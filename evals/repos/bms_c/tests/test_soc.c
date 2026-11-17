/**
 * @file test_soc.c
 * @brief Unit tests for the coulomb-counting state of charge.
 */
#include "soc.h"
#include "test_assert.h"

static void test_init_validates(void)
{
    CHECK(soc_init(0U, 50U) == BMS_ERR_PARAM);
    CHECK(soc_init(1000U, 101U) == BMS_ERR_PARAM);
    CHECK(soc_init(1000U, 50U) == BMS_OK);
    CHECK(soc_get_pct() == 50U);
}

static void test_charge_and_discharge(void)
{
    CHECK(soc_init(1000U, 50U) == BMS_OK);
    soc_update(1000, 360000U); /* 1 A for 6 min = 100 mAh = 10 % */
    CHECK(soc_get_pct() == 60U);
    soc_update(-2000, 360000U); /* -2 A for 6 min = -200 mAh = -20 % */
    CHECK(soc_get_pct() == 40U);
}

static void test_many_small_steps_do_not_drift(void)
{
    CHECK(soc_init(1000U, 20U) == BMS_OK);
    for (uint32_t i = 0U; i < 3600U; i++) {
        soc_update(100, 1000U); /* 100 mA for 1 h in 1 s steps = 100 mAh */
    }
    CHECK(soc_get_pct() == 30U);
}

int main(void)
{
    test_init_validates();
    test_charge_and_discharge();
    test_many_small_steps_do_not_drift();
    return TEST_RESULT();
}

/* Hidden acceptance test for eval case c-soc-clamp (never shown to the agent). */
#include "soc.h"
#include "test_assert.h"

static void test_overcharge_saturates_and_recovers(void)
{
    CHECK(soc_init(1000U, 95U) == BMS_OK);
    soc_update(1000, 3600000U); /* +1000 mAh on a pack that only had room for 50 mAh */
    CHECK(soc_get_pct() == 100U);
    soc_update(-100, 360000U); /* -10 mAh = -1 % */
    CHECK(soc_get_pct() == 99U);
}

static void test_deep_discharge_saturates_and_recovers(void)
{
    CHECK(soc_init(1000U, 5U) == BMS_OK);
    soc_update(-1000, 3600000U); /* -1000 mAh on a pack holding 50 mAh */
    CHECK(soc_get_pct() == 0U);
    soc_update(100, 360000U); /* +10 mAh = +1 % */
    CHECK(soc_get_pct() == 1U);
}

static void test_extreme_steps(void)
{
    CHECK(soc_init(1000U, 50U) == BMS_OK);
    soc_update(INT32_MIN, UINT32_MAX);
    CHECK(soc_get_pct() == 0U);
    soc_update(INT32_MAX, UINT32_MAX);
    CHECK(soc_get_pct() == 100U);
}

static void test_normal_operation_unchanged(void)
{
    CHECK(soc_init(1000U, 50U) == BMS_OK);
    soc_update(1000, 360000U);
    CHECK(soc_get_pct() == 60U);
    soc_update(-2000, 360000U);
    CHECK(soc_get_pct() == 40U);
}

int main(void)
{
    test_overcharge_saturates_and_recovers();
    test_deep_discharge_saturates_and_recovers();
    test_extreme_steps();
    test_normal_operation_unchanged();
    return TEST_RESULT();
}

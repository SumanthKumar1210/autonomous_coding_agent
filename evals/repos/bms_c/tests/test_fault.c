/**
 * @file test_fault.c
 * @brief Unit tests for the fault manager.
 */
#include "fault.h"
#include "test_assert.h"

static void test_set_and_clear(void)
{
    fault_init();
    CHECK(!fault_any_active());
    CHECK(fault_set(FAULT_SENSOR) == BMS_OK);
    CHECK(fault_is_active(FAULT_SENSOR));
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
    CHECK(fault_any_active());
    CHECK(fault_clear(FAULT_SENSOR) == BMS_OK);
    CHECK(!fault_any_active());
}

static void test_unknown_code(void)
{
    fault_init();
    CHECK(fault_set(FAULT_COUNT) == BMS_ERR_PARAM);
    CHECK(fault_clear(FAULT_COUNT) == BMS_ERR_PARAM);
    CHECK(!fault_is_active(FAULT_COUNT));
    CHECK(!fault_any_active());
}

int main(void)
{
    test_set_and_clear();
    test_unknown_code();
    return TEST_RESULT();
}

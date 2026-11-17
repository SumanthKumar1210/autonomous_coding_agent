/**
 * @file test_cell_monitor.c
 * @brief Unit tests for cell voltage monitoring.
 */
#include "cell_monitor.h"
#include "fault.h"
#include "hal_stub.h"
#include "test_assert.h"

static void setup(void)
{
    hal_stub_reset();
    fault_init();
    cell_monitor_init();
}

static void test_min_max(void)
{
    setup();
    hal_stub_set_cell_mv(0U, 3650U);
    hal_stub_set_cell_mv(2U, 3810U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(cell_monitor_min_mv() == 3650U);
    CHECK(cell_monitor_max_mv() == 3810U);

    uint16_t mv = 0U;
    CHECK(cell_monitor_cell_mv(2U, &mv) == BMS_OK);
    CHECK(mv == 3810U);
    CHECK(cell_monitor_cell_mv(BMS_CELL_COUNT, &mv) == BMS_ERR_PARAM);
    CHECK(cell_monitor_cell_mv(0U, NULL) == BMS_ERR_PARAM);
    CHECK(!fault_any_active());
}

static void test_overvoltage_latches_until_reset(void)
{
    setup();
    hal_stub_set_cell_mv(1U, 4250U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(fault_is_active(FAULT_CELL_OVERVOLTAGE));
    CHECK(cell_monitor_reset_faults() == BMS_ERR_STATE);
    hal_stub_set_cell_mv(1U, 4100U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(fault_is_active(FAULT_CELL_OVERVOLTAGE));
    CHECK(cell_monitor_reset_faults() == BMS_OK);
    CHECK(!fault_is_active(FAULT_CELL_OVERVOLTAGE));
}

static void test_partial_reset(void)
{
    setup();
    hal_stub_set_cell_mv(0U, 4300U);
    hal_stub_set_cell_mv(1U, 2600U);
    CHECK(cell_monitor_update() == BMS_OK);
    hal_stub_set_cell_mv(0U, 3700U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(cell_monitor_reset_faults() == BMS_ERR_STATE);
    CHECK(!fault_is_active(FAULT_CELL_OVERVOLTAGE));
    CHECK(fault_is_active(FAULT_CELL_UNDERVOLTAGE));
}

static void test_undervoltage(void)
{
    setup();
    hal_stub_set_cell_mv(3U, 2700U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(fault_is_active(FAULT_CELL_UNDERVOLTAGE));
    CHECK(!fault_is_active(FAULT_CELL_OVERVOLTAGE));
}

static void test_sensor_failure_keeps_previous_values(void)
{
    setup();
    CHECK(cell_monitor_update() == BMS_OK);
    hal_stub_fail_cell(2U, true);
    hal_stub_set_cell_mv(0U, 3000U);
    CHECK(cell_monitor_update() == BMS_ERR_HW);
    CHECK(fault_is_active(FAULT_SENSOR));
    CHECK(cell_monitor_min_mv() == 3700U);
    hal_stub_fail_cell(2U, false);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(!fault_is_active(FAULT_SENSOR));
}

int main(void)
{
    test_min_max();
    test_overvoltage_latches_until_reset();
    test_partial_reset();
    test_undervoltage();
    test_sensor_failure_keeps_previous_values();
    return TEST_RESULT();
}

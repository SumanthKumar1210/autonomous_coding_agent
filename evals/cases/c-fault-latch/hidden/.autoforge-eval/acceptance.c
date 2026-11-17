/* Hidden acceptance test for eval case c-fault-latch (never shown to the agent). */
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

static void test_overvoltage_latches_until_reset(void)
{
    setup();
    hal_stub_set_cell_mv(1U, 4300U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(fault_is_active(FAULT_CELL_OVERVOLTAGE));
    hal_stub_set_cell_mv(1U, 3700U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(fault_is_active(FAULT_CELL_OVERVOLTAGE));
    CHECK(cell_monitor_reset_faults() == BMS_OK);
    CHECK(!fault_is_active(FAULT_CELL_OVERVOLTAGE));
}

static void test_reset_refused_while_condition_present(void)
{
    setup();
    hal_stub_set_cell_mv(2U, 2600U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(cell_monitor_reset_faults() == BMS_ERR_STATE);
    CHECK(fault_is_active(FAULT_CELL_UNDERVOLTAGE));
}

static void test_partial_reset(void)
{
    setup();
    hal_stub_set_cell_mv(0U, 4300U);
    hal_stub_set_cell_mv(3U, 2600U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(fault_is_active(FAULT_CELL_OVERVOLTAGE));
    CHECK(fault_is_active(FAULT_CELL_UNDERVOLTAGE));

    hal_stub_set_cell_mv(0U, 3700U); /* overvoltage gone, undervoltage still present */
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(cell_monitor_reset_faults() == BMS_ERR_STATE);
    CHECK(!fault_is_active(FAULT_CELL_OVERVOLTAGE));
    CHECK(fault_is_active(FAULT_CELL_UNDERVOLTAGE));
}

static void test_reset_uses_last_measurement(void)
{
    setup();
    hal_stub_set_cell_mv(1U, 4300U);
    CHECK(cell_monitor_update() == BMS_OK);
    hal_stub_set_cell_mv(1U, 3700U); /* not measured yet */
    CHECK(cell_monitor_reset_faults() == BMS_ERR_STATE);
    CHECK(fault_is_active(FAULT_CELL_OVERVOLTAGE));
}

static void test_reset_with_nothing_active(void)
{
    setup();
    CHECK(cell_monitor_reset_faults() == BMS_OK);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(cell_monitor_reset_faults() == BMS_OK);
    CHECK(!fault_any_active());
}

static void test_sensor_fault_still_clears_itself(void)
{
    setup();
    hal_stub_fail_cell(0U, true);
    CHECK(cell_monitor_update() == BMS_ERR_HW);
    CHECK(fault_is_active(FAULT_SENSOR));
    hal_stub_fail_cell(0U, false);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(!fault_is_active(FAULT_SENSOR));
}

int main(void)
{
    test_overvoltage_latches_until_reset();
    test_reset_refused_while_condition_present();
    test_partial_reset();
    test_reset_uses_last_measurement();
    test_reset_with_nothing_active();
    test_sensor_fault_still_clears_itself();
    return TEST_RESULT();
}

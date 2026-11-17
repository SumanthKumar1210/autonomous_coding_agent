/* Hidden acceptance test for eval case c-overcurrent-protection (never shown to the agent). */
#include "contactor.h"
#include "fault.h"
#include "hal_stub.h"
#include "overcurrent.h"
#include "test_assert.h"

static void setup(void)
{
    hal_stub_reset();
    fault_init();
    contactor_init();
    overcurrent_init();
    CHECK(contactor_close() == BMS_OK);
}

static void sample(int32_t ma, uint32_t now_ms)
{
    hal_stub_set_current_ma(ma);
    hal_stub_set_time_ms(now_ms);
    CHECK(overcurrent_update() == BMS_OK);
}

static void test_constants(void)
{
    CHECK(OVERCURRENT_LIMIT_MA == 150000);
    CHECK(OVERCURRENT_DEBOUNCE_MS == 100U);
}

static void test_debounce_boundary_opens_contactor(void)
{
    setup();
    sample(150001, 1000U);
    sample(150001, 1099U);
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
    CHECK(contactor_is_closed());
    sample(150001, 1100U);
    CHECK(fault_is_active(FAULT_OVERCURRENT));
    CHECK(!contactor_is_closed());
    CHECK(!hal_stub_contactor_closed());
}

static void test_at_limit_restarts_debounce(void)
{
    setup();
    sample(200000, 0U);
    sample(150000, 60U); /* at the limit: not above, restarts */
    sample(200000, 120U);
    sample(200000, 200U);
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
    sample(200000, 220U);
    CHECK(fault_is_active(FAULT_OVERCURRENT));
}

static void test_discharge_direction(void)
{
    setup();
    sample(-150001, 0U);
    sample(-150001, 100U);
    CHECK(fault_is_active(FAULT_OVERCURRENT));

    setup();
    sample(INT32_MIN, 0U);
    sample(INT32_MIN, 150U);
    CHECK(fault_is_active(FAULT_OVERCURRENT));

    setup();
    sample(-150000, 0U);
    sample(-150000, 500U);
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
}

static void test_debounce_across_time_wrap(void)
{
    setup();
    sample(300000, 0xFFFFFFC0U); /* 64 ms before the wrap */
    sample(300000, 20U);         /* 84 ms later */
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
    sample(300000, 36U); /* 100 ms later */
    CHECK(fault_is_active(FAULT_OVERCURRENT));
}

static void test_latch_and_reset(void)
{
    setup();
    sample(200000, 0U);
    sample(200000, 100U);
    CHECK(fault_is_active(FAULT_OVERCURRENT));

    sample(200000, 150U);
    CHECK(overcurrent_reset() == BMS_ERR_STATE);
    CHECK(fault_is_active(FAULT_OVERCURRENT));

    sample(0, 200U);
    CHECK(fault_is_active(FAULT_OVERCURRENT)); /* latched: update never clears it */
    CHECK(overcurrent_reset() == BMS_OK);
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
}

int main(void)
{
    test_constants();
    test_debounce_boundary_opens_contactor();
    test_at_limit_restarts_debounce();
    test_discharge_direction();
    test_debounce_across_time_wrap();
    test_latch_and_reset();
    return TEST_RESULT();
}

/* Hidden acceptance test for eval case c-pyro-fuse (never shown to the agent). */
#include "hal_stub.h"
#include "pyro_fuse.h"
#include "test_assert.h"

static void test_fires_once(void)
{
    hal_stub_reset();
    pyro_fuse_init();
    CHECK(!pyro_fuse_is_fired());
    CHECK(hal_stub_pyro_fire_count() == 0U);

    CHECK(pyro_fuse_fire() == BMS_OK);
    CHECK(pyro_fuse_is_fired());
    CHECK(hal_stub_pyro_fire_count() == 1U);

    CHECK(pyro_fuse_fire() == BMS_ERR_STATE);
    CHECK(pyro_fuse_fire() == BMS_ERR_STATE);
    CHECK(hal_stub_pyro_fire_count() == 1U);
}

static void test_hardware_failure_never_retries(void)
{
    hal_stub_reset();
    pyro_fuse_init();
    hal_stub_fail_pyro(true);
    CHECK(pyro_fuse_fire() == BMS_ERR_HW);
    CHECK(pyro_fuse_is_fired());
    CHECK(hal_stub_pyro_fire_count() == 1U);

    hal_stub_fail_pyro(false);
    CHECK(pyro_fuse_fire() == BMS_ERR_STATE);
    CHECK(hal_stub_pyro_fire_count() == 1U);
}

static void test_init_rearms_and_reset_clears_stub(void)
{
    hal_stub_reset();
    pyro_fuse_init();
    CHECK(pyro_fuse_fire() == BMS_OK);
    pyro_fuse_init();
    CHECK(!pyro_fuse_is_fired());
    hal_stub_reset();
    CHECK(hal_stub_pyro_fire_count() == 0U);
    CHECK(pyro_fuse_fire() == BMS_OK);
}

int main(void)
{
    test_fires_once();
    test_hardware_failure_never_retries();
    test_init_rearms_and_reset_clears_stub();
    return TEST_RESULT();
}

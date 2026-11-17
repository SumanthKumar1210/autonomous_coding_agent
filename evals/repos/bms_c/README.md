# bms_c

Battery management firmware for a 4-cell (4S) pack. Evaluation fixture for
AutoForge; it is frozen and only changes together with the eval cases.

## Layout

- `include/` - public headers, one per module (`<module>.h`)
- `src/` - module implementations (`<module>.c`)
- `tests/` - one unit test program per module (`test_<module>.c`), plus the
  HAL stub (`hal_stub.c`) that tests use instead of real hardware

## Conventions

- C11, built with `-Wall -Wextra -Werror -pedantic`.
- Fixed-width integer types from `<stdint.h>`; no plain `int` for physical values.
- Every function that can fail returns `bms_status_t`; outputs go through
  pointer parameters, which are checked for `NULL`.
- Hardware is only touched through `hal.h`. Tests control the hardware through
  the `hal_stub_*` functions in `tests/hal_stub.h`; every new HAL function needs
  a stub there.
- Module state is file-level `static`, and every stateful module has an
  `<module>_init()` that resets it, so one test program can run several scenarios.
- Doxygen comments (`/** ... */`) on every public declaration.
- Every new module is added to `BMS_SOURCES` and `BMS_MODULES` in
  `CMakeLists.txt`, so it is built and its unit test runs with CTest.

## Build and test

```sh
cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure
```

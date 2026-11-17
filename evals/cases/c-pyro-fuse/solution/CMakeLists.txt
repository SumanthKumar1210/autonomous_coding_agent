cmake_minimum_required(VERSION 3.16)
project(bms_c C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Werror -pedantic)

# Every module in src/ is listed here and has a unit test tests/test_<module>.c.
set(BMS_MODULES cell_monitor contactor fault pyro_fuse soc)
set(BMS_SOURCES
    src/cell_monitor.c
    src/contactor.c
    src/fault.c
    src/pyro_fuse.c
    src/soc.c
)

add_library(bms STATIC ${BMS_SOURCES})
target_include_directories(bms PUBLIC include)

# Hardware stub used by the unit tests instead of the real HAL.
add_library(hal_stub STATIC tests/hal_stub.c)
target_include_directories(hal_stub PUBLIC include tests)

enable_testing()
foreach(module ${BMS_MODULES})
    add_executable(test_${module} tests/test_${module}.c)
    target_link_libraries(test_${module} PRIVATE bms hal_stub)
    add_test(NAME ${module} COMMAND test_${module})
endforeach()

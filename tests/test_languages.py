"""Tests for agents.languages: include/import resolution, definitions, related files."""

from __future__ import annotations

from autoforge.agents.languages import CLanguage, PythonLanguage, language_for, languages_in

C = CLanguage()
PY = PythonLanguage()

C_PATHS = frozenset({
    "fw/include/bms/can.h", "fw/include/bms/types.h", "fw/src/can.c", "fw/src/local.h", "fw/test/test_can.c",
    "other/can.h",
})


def test_language_for_extension() -> None:
    """Files map to a language by extension; other files have none."""
    assert language_for("a/b.c") is not None and language_for("a/b.H") is not None
    assert language_for("a/b.py") is not None
    assert language_for("README.md") is None
    assert [lang.name for lang in languages_in(["x.py", "y.c", "z.md"])] == ["C", "Python"]


def test_c_includes_resolve_local_then_closest_suffix_match() -> None:
    """Local "x.h" wins; <bms/x.h> resolves through include folders to the closest match; system headers vanish."""
    src = '#include "local.h"\n#include "bms/can.h"\n#include <bms/types.h>\n#include <stdint.h>\n'

    assert C.imports("fw/src/can.c", src, C_PATHS) == ["fw/src/local.h", "fw/include/bms/can.h",
                                                       "fw/include/bms/types.h"]


def test_c_include_ambiguity_prefers_same_tree() -> None:
    """With two can.h files, the one sharing the longest folder prefix with the includer is chosen."""
    assert C.imports("other/x.c", '#include "can.h"\n', C_PATHS) == ["other/can.h"]


def test_c_definitions() -> None:
    """Functions (definitions and prototypes), macros, typedefs, tags and enumerators are found; comments ignored."""
    code = """\
#define CAN_DLC (8U)
/* static void commented_out(void) { } */
typedef uint16_t can_mv_t;
typedef enum
{
    CAN_OK = 0,
    CAN_ERR, /* error */
} can_status_t;
struct can_frame { int x; };
static bms_status_t encode_temp(int16_t temp);
const can_frame_t *can_get(void)
{
    if (x) { return foo(1); }
}
"""
    defs = C.definitions(code)

    assert {"CAN_DLC", "can_mv_t", "CAN_OK", "CAN_ERR", "can_status_t", "can_frame", "encode_temp",
            "can_get"} <= defs
    assert "commented_out" not in defs and "foo" not in defs and "if" not in defs


def test_c_related_header_source_and_tests() -> None:
    """A .c file's header (closest one) and its unit test are related; and the other way round."""
    assert C.related("fw/src/can.c", C_PATHS) == [("fw/include/bms/can.h", "header of fw/src/can.c"),
                                                  ("fw/test/test_can.c", "unit test of fw/src/can.c")]
    assert C.related("fw/include/bms/can.h", C_PATHS)[0] == ("fw/src/can.c", "implementation of fw/include/bms/can.h")


def test_python_imports() -> None:
    """Absolute, from-, submodule, src-layout and relative imports resolve; third-party ones do not."""
    paths = frozenset({"src/pkg/__init__.py", "src/pkg/core.py", "src/pkg/util/helpers.py", "src/pkg/util/__init__.py",
                       "tests/test_core.py"})
    code = ("import os\nimport pkg.core\nfrom pkg.util import helpers\nfrom . import core\n"
            "from .util.helpers import thing\nimport numpy as np\n")

    found = PY.imports("src/pkg/main.py", code, paths)

    assert found == ["src/pkg/core.py", "src/pkg/util/__init__.py", "src/pkg/util/helpers.py", "src/pkg/__init__.py"]


def test_python_definitions_and_tests() -> None:
    """Functions, classes and module constants are definitions; test_<name>.py is related."""
    code = "MAX_V = 4.2\nclass SocEstimator:\n    def update(self):\n        pass\nasync def run(): ...\n"
    defs = PY.definitions(code)

    assert {"MAX_V", "SocEstimator", "update", "run"} <= defs
    assert PY.related("src/pkg/core.py", frozenset({"tests/test_core.py"})) == [
        ("tests/test_core.py", "unit test of src/pkg/core.py")]

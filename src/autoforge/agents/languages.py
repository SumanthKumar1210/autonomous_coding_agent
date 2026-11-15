"""Language handling: everything AutoForge knows about Python and C, behind one small interface.

Context retrieval (``agents.context``) and the agent prompts ask a
``Language`` four things:

- **imports**: which repository files does this file depend on
  (``#include "x.h"``, ``from pkg import mod``)?
- **definitions**: which symbols does this file define (functions, types,
  macros, classes, constants)? Used to find the file a ticket talks about.
- **related**: which files belong with this one (C header <-> source, unit
  tests)?
- **rules**: language-specific instructions for the coder and the reviewer.

Everything is deterministic text analysis (regular expressions), no compiler
and no new dependency. That is precise enough to *choose context*; it is not a
parser and is never used to validate code. Adding a language means adding one
class to ``LANGUAGES``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import ClassVar, Protocol

__all__ = ["LANGUAGES", "CLanguage", "Language", "PythonLanguage", "language_for", "languages_in"]


class Language(Protocol):
    """What context retrieval and the prompts need to know about one programming language."""

    name: ClassVar[str]
    """Display name, e.g. ``"C"``."""
    suffixes: ClassVar[frozenset[str]]
    """File extensions (lower-case, with dot) of this language."""
    coder_rules: ClassVar[str]
    """Extra instructions for the coder when the repository uses this language."""
    reviewer_rules: ClassVar[str]
    """Extra checks for the reviewer when the repository uses this language."""

    def imports(self, path: str, content: str, paths: frozenset[str]) -> list[str]:
        """Return the repository files that ``path`` depends on, resolved to existing paths."""
        ...

    def definitions(self, content: str) -> set[str]:
        """Return the names of symbols defined or declared in ``content``."""
        ...

    def related(self, path: str, paths: frozenset[str]) -> list[tuple[str, str]]:
        """Return ``(path, reason)`` pairs of files that belong with ``path`` (header/source, tests)."""
        ...


def _stem(path: str) -> str:
    """File name without extension, lower-case: ``"src/Cell_Monitor.c"`` -> ``"cell_monitor"``."""
    return PurePosixPath(path).stem.lower()


def _closest(candidates: list[str], near: str) -> str:
    """Pick the candidate path sharing the longest directory prefix with ``near`` (ties: shortest path)."""
    near_parts = PurePosixPath(near).parts

    def shared(c: str) -> int:
        n = 0
        for a, b in zip(PurePosixPath(c).parts, near_parts, strict=False):
            if a != b:
                break
            n += 1
        return n

    return min(candidates, key=lambda c: (-shared(c), len(c), c))


def _tests_for(path: str, paths: frozenset[str], patterns: tuple[str, ...]) -> list[tuple[str, str]]:
    """Find unit-test files for ``path`` by name, e.g. ``test_soc.py`` / ``soc_test.c``."""
    stem = _stem(path)
    names = {p.format(stem=stem) for p in patterns}
    return [(p, f"unit test of {path}") for p in sorted(paths)
            if p != path and PurePosixPath(p).name.lower() in names]


class CLanguage:
    """C (and C headers): the main automotive embedded target."""

    name: ClassVar[str] = "C"
    suffixes: ClassVar[frozenset[str]] = frozenset({".c", ".h"})

    _INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*["<]([^">]+)[">]', re.MULTILINE)
    _FUNCTION = re.compile(
        r"^(?!\s)(?:[A-Za-z_]\w*[\s*]+)+?\**([A-Za-z_]\w*)\s*\(", re.MULTILINE)
    _MACRO = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)", re.MULTILINE)
    _TAG = re.compile(r"\b(?:struct|enum|union)\s+([A-Za-z_]\w*)\s*\{")
    _TYPEDEF_END = re.compile(r"\}\s*([A-Za-z_]\w*)\s*;")
    _TYPEDEF_SIMPLE = re.compile(r"^[ \t]*typedef\s+[^;{]*?\b([A-Za-z_]\w*)\s*;", re.MULTILINE)
    _ENUMERATOR = re.compile(r"^[ \t]+([A-Z][A-Z0-9_]*)\s*(?:=[^,\n]*)?,?[ \t]*(?:/[/*].*)?$", re.MULTILINE)
    _KEYWORDS: ClassVar[frozenset[str]] = frozenset(
        {"if", "for", "while", "switch", "return", "sizeof", "else", "do", "case", "defined"})

    coder_rules: ClassVar[str] = """\
C rules (embedded target):
- Follow the repository's C standard, file layout and naming. If nothing says \
otherwise, write C99.
- Use fixed-width types from <stdint.h> and bool from <stdbool.h>. Avoid plain \
int for sized data.
- Public API: declare it in the module's header, with a short comment for each \
function. Define it in the .c file. Keep header and source signatures \
identical. Give internal functions and file-scope data static linkage.
- Every header has an include guard in the repository's style.
- No dynamic memory (malloc/free), no recursion, no variable-length arrays.
- Check pointer arguments for NULL and array indices against their bounds. \
Return errors the way the module already does (status enum or return code).
- No magic numbers. Use the named constants or configuration header that the \
repository already has, or add new ones there.
- Watch for integer overflow, signed/unsigned mixing and division by zero. \
Make narrowing conversions explicit.
- If you add a .c file and the build (CMakeLists.txt or Makefile) lists \
sources explicitly, add it there too.
"""

    reviewer_rules: ClassVar[str] = """\
C checks:
- Header and source declarations match. New .c files are added to the build \
when it lists sources explicitly. Include guards are present.
- No undefined behaviour: overflow, out-of-bounds access, uninitialised \
variables, NULL dereference, signed shifts, division by zero.
- Integer types: fixed-width types, correct signedness and units, explicit \
narrowing conversions.
- No malloc/free, recursion or variable-length arrays, unless the codebase \
already uses them.
- Errors are reported the way the module already does. No error is silently \
ignored.
"""

    def imports(self, path: str, content: str, paths: frozenset[str]) -> list[str]:
        """Resolve ``#include`` directives to repository files.

        ``"x.h"`` is looked up next to the including file first. Otherwise,
        and for ``<bms/x.h>`` style includes, any repository path that ends with
        the include name is a candidate (that covers ``-I include/`` folders),
        and the one closest to the including file wins. System headers such as
        ``<stdint.h>`` do not resolve and are ignored.
        """
        found: list[str] = []
        folder = PurePosixPath(path).parent
        for inc in self._INCLUDE.findall(content):
            inc = inc.strip()
            local = (folder / inc).as_posix()
            if local in paths:
                target = local
            else:
                candidates = [p for p in paths if p == inc or p.endswith("/" + inc)]
                if not candidates:
                    continue
                target = _closest(candidates, path)
            if target != path and target not in found:
                found.append(target)
        return found

    def definitions(self, content: str) -> set[str]:
        """Names of functions (definitions and prototypes), macros, struct/enum/union tags, typedefs, enumerators."""
        code = _strip_c_comments(content)
        names = {m for m in self._FUNCTION.findall(code) if m not in self._KEYWORDS}
        names.update(self._MACRO.findall(code))
        names.update(self._TAG.findall(code))
        names.update(self._TYPEDEF_END.findall(code))
        names.update(self._TYPEDEF_SIMPLE.findall(code))
        names.update(self._ENUMERATOR.findall(code))
        return names

    def related(self, path: str, paths: frozenset[str]) -> list[tuple[str, str]]:
        """The matching header/source (same name, closest folder) and unit tests of a C file."""
        out: list[tuple[str, str]] = []
        suffix = PurePosixPath(path).suffix.lower()
        partner = {".c": ".h", ".h": ".c"}.get(suffix)
        if partner:
            stem = _stem(path)
            candidates = [p for p in paths if _stem(p) == stem and PurePosixPath(p).suffix.lower() == partner]
            if candidates:
                kind = "header" if partner == ".h" else "implementation"
                out.append((_closest(candidates, path), f"{kind} of {path}"))
        out += _tests_for(path, paths, ("test_{stem}.c", "{stem}_test.c", "test_{stem}.cpp",
                                        "{stem}_test.cpp", "{stem}_tests.c", "tst_{stem}.c"))
        return out


class PythonLanguage:
    """Python (tooling, host-side code, prototypes)."""

    name: ClassVar[str] = "Python"
    suffixes: ClassVar[frozenset[str]] = frozenset({".py", ".pyi"})

    _IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+(?:[ \t]*,[ \t]*[\w.]+)*)", re.MULTILINE)
    _FROM = re.compile(r"^[ \t]*from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+\(?([\w ,\t*]+)", re.MULTILINE)
    _DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_]\w*)", re.MULTILINE)
    _CONST = re.compile(r"^([A-Za-z_]\w*)[ \t]*(?::[^=\n]+)?=", re.MULTILINE)

    coder_rules: ClassVar[str] = """\
Python rules:
- Match the repository's Python version, style and layout. Use type hints on \
public functions and a docstring that states units.
- Use only the standard library and dependencies the repository already \
declares. Do not add new ones.
- Validate inputs. Raise specific exceptions with clear messages. Do not fail \
silently.
"""

    reviewer_rules: ClassVar[str] = """\
Python checks:
- No new third-party dependencies. Imports resolve to existing modules.
- Inputs are validated and units are consistent. Exceptions are not swallowed.
"""

    def imports(self, path: str, content: str, paths: frozenset[str]) -> list[str]:
        """Resolve ``import a.b`` / ``from a.b import c`` / ``from . import c`` to repository files.

        A module ``a.b`` matches ``a/b.py`` or ``a/b/__init__.py``, also under a
        source folder (any path ending in ``/a/b.py``, e.g. ``src/a/b.py``).
        ``from a import b`` also tries the submodule ``a/b.py``. Third-party and
        standard-library modules do not resolve and are ignored.
        """
        modules: list[str] = []
        for group in self._IMPORT.findall(content):
            modules += [m.strip() for m in group.split(",")]
        package = PurePosixPath(path).parent
        for base, names in self._FROM.findall(content):
            dots = len(base) - len(base.lstrip("."))
            if dots:
                anchor = package
                for _ in range(dots - 1):
                    anchor = anchor.parent
                prefix = ".".join(p for p in (*anchor.parts, base.lstrip(".")) if p and p != ".")
            else:
                prefix = base
            if prefix:
                modules.append(prefix)
            modules += [f"{prefix}.{n.strip()}" if prefix else n.strip()
                        for n in names.split(",") if n.strip() and n.strip() != "*"]
        found: list[str] = []
        for module in modules:
            target = self._resolve(module, path, paths)
            if target and target != path and target not in found:
                found.append(target)
        return found

    def definitions(self, content: str) -> set[str]:
        """Names of functions, classes and module-level assignments."""
        return set(self._DEF.findall(content)) | set(self._CONST.findall(content))

    def related(self, path: str, paths: frozenset[str]) -> list[tuple[str, str]]:
        """Unit tests of a Python module (``test_<name>.py`` / ``<name>_test.py``)."""
        if PurePosixPath(path).name.lower().startswith("test_"):
            return []
        return _tests_for(path, paths, ("test_{stem}.py", "{stem}_test.py"))

    @staticmethod
    def _resolve(module: str, near: str, paths: frozenset[str]) -> str | None:
        """Map a dotted module name to a repository file, or ``None``."""
        rel = module.replace(".", "/")
        if not rel:
            return None
        for suffix in (".py", "/__init__.py"):
            exact = rel + suffix
            candidates = [p for p in paths if p == exact or p.endswith("/" + exact)]
            if candidates:
                return _closest(candidates, near)
        return None


LANGUAGES: tuple[Language, ...] = (CLanguage(), PythonLanguage())
"""Every supported language. Add a class here to support another one."""


def language_for(path: str) -> Language | None:
    """Return the language of a file by its extension, or ``None`` for other files."""
    suffix = PurePosixPath(path).suffix.lower()
    return next((lang for lang in LANGUAGES if suffix in lang.suffixes), None)


def languages_in(paths: Iterable[str]) -> list[Language]:
    """Return the supported languages used by any of ``paths``, in ``LANGUAGES`` order."""
    present = {type(lang) for p in paths if (lang := language_for(p))}
    return [lang for lang in LANGUAGES if type(lang) in present]


_C_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def _strip_c_comments(code: str) -> str:
    """Remove ``/* */`` and ``//`` comments, keeping line breaks so line-anchored patterns still work."""
    return _C_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), code)

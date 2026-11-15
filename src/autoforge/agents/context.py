"""Repository context for agents: which files the LLM gets to read, and why.

LLM context is limited and costs money, and irrelevant files make the output
worse, so AutoForge does not send the whole repository. Selection is
**ticket-driven and deterministic** (no embeddings, no LLM call):

1. **Read** the repository's text files (one archive download for large repos).
2. **Extract terms from the ticket**: file names, code identifiers
   (``cell_monitor_update``, ``SocEstimator``, things in backticks or followed
   by ``(``) and keywords (``overvoltage``, ``CAN``).
3. **Score every file**: named in the ticket, *defines* a ticket identifier,
   uses one, its path matches a keyword, its content mentions rare keywords.
4. **Pick seeds**: the best-scoring files (``MAX_SEEDS``).
5. **Expand** along the code's own structure, using ``agents.languages``:
   the C header/source partner, ``#include``s / imports, unit tests, a few
   files that *use* the seed (callers), and nearby build/convention files
   (README, CMakeLists.txt, .clang-format ...).
6. **Fill the byte budget** in that priority order. Relevant files too large to
   show in full are shown as **excerpts** (file head + the lines around ticket
   identifiers). Excerpts are read-only for the agent: ``domain.policy`` only
   allows modifying files whose full content was shown.

If the ticket matches nothing, it falls back to a general sample of the repo
(``select_context_files``). Every choice is recorded as a ``ContextFile``
with a reason, logged, and listed in the PR, so a reviewer can see what the
agent was looking at.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from autoforge.agents.languages import Language, language_for
from autoforge.domain.models import ContextFile, RepoFile, RepoSnapshot, Ticket
from autoforge.ports import RepoHost

log = logging.getLogger(__name__)

MAX_SCAN_FILES = 3000
"""Most text files read for scoring per run (source files first)."""
MAX_SCAN_FILE_BYTES = 512_000
"""Files larger than this are scored by path only (never read)."""
MAX_SEEDS = 6
"""Most files picked directly from ticket matches."""
SEED_MIN_SCORE = 12
"""Minimum score for a seed (e.g. one path keyword, or one defined identifier)."""
SEED_RELATIVE = 0.4
"""A seed must also score at least this fraction of the best file's score."""
MAX_USERS_PER_SEED = 2
"""Most files that include/import a seed (its callers) added per seed."""
HUB_USERS = 4
"""A file included/imported by more files than this is a hub (e.g. a types header); its users are not added."""
MAX_STYLE_SIBLINGS = 2
"""Most same-folder, same-language files added as style references."""
STYLE_SIBLINGS_BELOW = 3
"""Style references are added only if fewer files of the seed's language were selected."""
EXCERPT_BYTES = 6_000
"""Size budget of one excerpt."""
EXCERPT_HEAD_LINES = 25
"""Every excerpt starts with this many lines (includes, module docstring)."""
EXCERPT_WINDOW = 8
"""Lines kept before and after a line that mentions a ticket identifier."""

_CONVENTION_FILES = {
    "readme.md", "readme.rst", "readme", "contributing.md", "pyproject.toml", "setup.cfg",
    "setup.py", "requirements.txt", "makefile", "cmakelists.txt", ".clang-format", "ruff.toml",
    ".editorconfig",
}
"""File names (lower-case) that describe how the project is built and styled."""

_SOURCE_SUFFIXES = {".py", ".pyi", ".c", ".h", ".cpp", ".hpp"}
"""Source-code extensions, preferred over other text files."""

_TEXT_SUFFIXES = _SOURCE_SUFFIXES | {".toml", ".cfg", ".ini", ".md", ".rst", ".txt", ".yml", ".yaml", ".json", ".cmake"}
"""All extensions considered text and therefore eligible for the context."""

_SYMLINK_MODE = "120000"
"""Git file mode for symbolic links (never included: content is just a target path)."""

_STOPWORDS = frozenset("""
a about above after again all also an and any are as at be because been before being below between both but by
can could did do does doing done down during each either else etc few for from further get gets given had has
have having he her here hers how however if in into is it its itself just let like make makes may me might more
most must my need needs no nor not now of off on once only or other our out over own per please same shall she
should so some such than that the their them then there these they this those through to too under until up upon
us use used uses using very via was we were what when where whether which while who why will with within without
would yet you your

add adds added implement implements implemented implementation create creates update updates support supports new
function functions method methods file files code module modules value values return returns result results ensure
change changes changed feature bug fix fixes issue ticket acceptance criteria criterion requirement requirements
expected behavior behaviour currently existing based example examples case cases handle handles handling
check checks correct correctly work works working simple basic proper properly following follow below above test
tests unit called call calls data type types number numbers string list set true false none null void int char
static const struct include define self def import class return
""".split())  # noqa: SIM905 (a word list reads better as text)
"""Words that say nothing about *where* in the code a change belongs."""

_PATHLIKE = re.compile(r"[\w./-]*\w\.[A-Za-z][A-Za-z0-9]{0,4}\b")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\(")
_BACKTICK = re.compile(r"`([^`\n]{1,200})`")
_SUBWORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")


def _norm(word: str) -> str:
    """Lower-case and strip a plural ``s``: ``"Voltages"`` -> ``"voltage"`` (``"status"`` stays)."""
    w = word.lower()
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith(("ss", "us", "is")) else w


def _subwords(identifier: str) -> list[str]:
    """Split an identifier into normalised words: ``cellVoltage_MAX2`` -> ``["cell", "voltage", "max"]``."""
    return [_norm(w) for w in _SUBWORD.findall(identifier)]


def _is_code_like(token: str) -> bool:
    """True for tokens that look like code identifiers rather than English words."""
    inner = token.strip("_")
    return len(token) >= 3 and ("_" in inner or bool(re.search(r"[a-z][A-Z][a-z]", token)))


@dataclass(frozen=True)
class TicketTerms:
    """What the ticket says, reduced to things that can be matched against code.

    Attributes:
        paths: Lower-cased path-like mentions, e.g. ``"src/can_rx.c"`` or ``"soc.py"``.
        identifiers: Code identifiers, case-sensitive, e.g. ``"cell_monitor_update"``.
        keywords: Normalised words, e.g. ``"overvoltage"``, ``"can"`` (from "CAN").
    """

    paths: frozenset[str] = frozenset()
    identifiers: frozenset[str] = frozenset()
    keywords: frozenset[str] = frozenset()


def extract_terms(ticket: Ticket) -> TicketTerms:
    """Extract file names, identifiers and keywords from the ticket's title, body and comments.

    The ticket is untrusted text, but it is only *matched* against the code
    here, never executed or sent anywhere, so there is nothing it can inject.
    """
    text = "\n".join([ticket.title, ticket.body, *ticket.comments])
    paths = {m.lower().removeprefix("./") for m in _PATHLIKE.findall(text) if not m.startswith(("http", "www."))}
    identifiers: set[str] = set(_CALL.findall(text))
    for span in _BACKTICK.findall(text):
        identifiers.update(t for t in _IDENT.findall(span) if len(t) >= 3)
    identifiers.update(t for t in _IDENT.findall(text) if _is_code_like(t))
    identifiers -= _STOPWORDS
    keywords = {w for token in _IDENT.findall(text) for w in _subwords(token) if len(w) >= 3}
    keywords -= _STOPWORDS
    keywords |= {a.lower() for a in _ACRONYM.findall(text)}  # "CAN", "SOC", "ADC" survive as keywords
    return TicketTerms(frozenset(paths), frozenset(identifiers), frozenset(keywords))


@dataclass
class _Doc:
    """One repository file as seen by the scorer."""

    file: RepoFile
    content: str | None
    language: Language | None
    identifiers: set[str] = field(default_factory=set)
    words: set[str] = field(default_factory=set)
    definitions: set[str] = field(default_factory=set)
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        """Repository-relative path."""
        return self.file.path


def build_snapshot(repo: RepoHost, ticket: Ticket, *, max_context_bytes: int, max_file_bytes: int) -> RepoSnapshot:
    """Build the ``RepoSnapshot`` the agents will work from, with context chosen for ``ticket``.

    Args:
        repo: The repository to read from.
        ticket: The ticket being implemented (drives which files are relevant).
        max_context_bytes: Total byte budget for file contents shown to the LLM.
        max_file_bytes: Files larger than this are shown only as excerpts.

    Returns:
        A snapshot with the base commit, every path's mode, the selected full
        files and excerpts, and the reason each one was chosen.
    """
    base = repo.get_base()
    all_files = repo.list_files(base)
    scan = sorted((f for f in all_files if _eligible(f) and f.size <= MAX_SCAN_FILE_BYTES),
                  key=lambda f: (_rank(f.path), f.size, f.path))[:MAX_SCAN_FILES]
    contents: dict[str, str] = {}
    for path, raw in repo.read_files(base, scan).items():
        try:
            contents[path] = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
    terms = extract_terms(ticket)
    files, excerpts, context = plan_context(all_files, contents, terms, max_context_bytes=max_context_bytes,
                                            max_file_bytes=max_file_bytes)
    log.info("repo snapshot built", extra={
        "branch": base.branch, "commit": base.commit_sha, "files_total": len(all_files),
        "files_scanned": len(contents), "files_in_context": len(files), "excerpts": len(excerpts),
        "context": [f"{c.path} ({c.reason}{', excerpt' if c.excerpt else ''})" for c in context],
    })
    return RepoSnapshot(base=base, modes={f.path: f.mode for f in all_files}, files=files, excerpts=excerpts,
                        context=context)


def plan_context(all_files: list[RepoFile], contents: dict[str, str], terms: TicketTerms, *,
                 max_context_bytes: int, max_file_bytes: int,
                 ) -> tuple[dict[str, str], dict[str, str], list[ContextFile]]:
    """Choose the context for one ticket (pure function: no I/O, easy to test and to evaluate).

    Args:
        all_files: Every file in the repository.
        contents: Text content of the files that were read (path -> text).
        terms: What the ticket mentions.
        max_context_bytes: Total byte budget for full files and excerpts.
        max_file_bytes: Largest file shown in full.

    Returns:
        ``(files, excerpts, context)``: full contents, excerpts, and the reason
        for every file shown, in priority order.
    """
    docs = {f.path: _make_doc(f, contents.get(f.path)) for f in all_files
            if f.mode != _SYMLINK_MODE and _eligible(f)}
    paths = frozenset(f.path for f in all_files)
    _score_all(docs, terms)
    ranked = sorted(docs.values(), key=lambda d: (-d.score, d.path))
    best = ranked[0].score if ranked else 0
    seeds = [d for d in ranked if d.score >= SEED_MIN_SCORE and d.score >= SEED_RELATIVE * best][:MAX_SEEDS]
    if not seeds:
        chosen = select_context_files(all_files, max_context_bytes=max_context_bytes, max_file_bytes=max_file_bytes)
        files = {f.path: contents[f.path] for f in chosen if f.path in contents}
        return files, {}, [ContextFile(p, "general context (no ticket match)") for p in files]

    plan: list[tuple[str, str, int]] = []  # (path, reason, tier); tier 0 = most important
    planned: set[str] = set()

    def add(path: str, reason: str, tier: int) -> None:
        if path in docs and path not in planned:
            planned.add(path)
            plan.append((path, reason, tier))

    for conv in _convention_files(paths, [s.path for s in seeds]):
        add(conv, "build/convention file", 0)
    for s in seeds:
        add(s.path, "; ".join(s.reasons[:2]), 1)
    core = [s.path for s in seeds if not _is_test(s.path)]  # tests are context, not a source of dependencies
    for s in seeds:
        if s.language:
            for rel, reason in s.language.related(s.path, paths):
                is_test = reason.startswith("unit test")
                add(rel, reason, 3 if is_test else 1)
                if not is_test:
                    core.append(rel)
    for path in core:
        doc = docs.get(path)
        if doc and doc.language and doc.content is not None:
            verb = "included by" if doc.language.name == "C" else "imported by"
            for dep in doc.language.imports(path, doc.content, paths):
                add(dep, f"{verb} {path}", 2)
    for s in seeds:
        users = _users_of(s.path, core, docs, paths)
        if len(users) <= HUB_USERS:
            for user in users[:MAX_USERS_PER_SEED]:
                add(user, f"uses {s.path}", 3)
    lead = seeds[0]
    same_language = sum(1 for p in planned if lead.language and docs[p].language is lead.language)
    if same_language < STYLE_SIBLINGS_BELOW:
        for sib in _style_siblings(lead, docs, planned)[:MAX_STYLE_SIBLINGS]:
            add(sib, f"same folder as {lead.path} (style reference)", 4)

    return _fill_budget(plan, docs, terms, max_context_bytes=max_context_bytes, max_file_bytes=max_file_bytes)


def select_context_files(files: list[RepoFile], *, max_context_bytes: int, max_file_bytes: int) -> list[RepoFile]:
    """Fallback selection when the ticket matches no file: a general sample of the repository.

    Rules:
        - only text files (by name/extension), never symlinks;
        - skip files larger than ``max_file_bytes``;
        - order by priority (convention files, then source, then other text),
          then by size (small first), then by path (stable order);
        - add files while they fit in ``max_context_bytes``.
    """
    candidates = [f for f in files if f.mode != _SYMLINK_MODE and f.size <= max_file_bytes and _eligible(f)]
    candidates.sort(key=lambda f: (_rank(f.path), f.size, f.path))
    selected: list[RepoFile] = []
    used = 0
    for f in candidates:
        if used + f.size <= max_context_bytes:
            selected.append(f)
            used += f.size
    return selected


# ----------------------------------------------------------------- scoring

def _make_doc(file: RepoFile, content: str | None) -> _Doc:
    """Index one file: identifiers, words and definitions (when its content was read)."""
    lang = language_for(file.path)
    doc = _Doc(file=file, content=content, language=lang)
    if content is not None:
        doc.identifiers = set(_IDENT.findall(content))
        doc.words = {w for ident in doc.identifiers for w in _subwords(ident)}
        if lang:
            doc.definitions = lang.definitions(content)
    return doc


def _score_all(docs: dict[str, _Doc], terms: TicketTerms) -> None:
    """Give every file a relevance score for the ticket, with human-readable reasons."""
    n = max(len(docs), 1)
    df: dict[str, int] = {k: 0 for k in terms.keywords}
    for d in docs.values():
        for k in terms.keywords & d.words:
            df[k] += 1
    for d in docs.values():
        lower = d.path.lower()
        if any(lower == m or lower.endswith("/" + m) for m in terms.paths):
            _credit(d, 100, "named in the ticket")
        defined = sorted(terms.identifiers & d.definitions)
        if defined:
            _credit(d, min(40 * len(defined), 120), "defines " + ", ".join(f"`{x}`" for x in defined[:3]))
        used = sorted((terms.identifiers & d.identifiers) - set(defined))
        if used:
            _credit(d, min(8 * len(used), 24), "uses " + ", ".join(f"`{x}`" for x in used[:3]))
        path_words = {w for part in PurePosixPath(lower).with_suffix("").parts for w in _subwords(part)}
        in_path = sorted(terms.keywords & path_words)
        if in_path:
            _credit(d, min(12 * len(in_path), 36), "path matches " + ", ".join(f"'{w}'" for w in in_path[:3]))
        rare = [k for k in sorted(terms.keywords & d.words) if df[k] <= max(1, n // 2)]
        if rare:
            points = sum(4 if df[k] <= max(1, n // 4) else 2 for k in rare)
            _credit(d, min(points, 24), "mentions " + ", ".join(f"'{w}'" for w in rare[:3]))
        if _is_test(d.path):
            d.score //= 2  # a test mentions everything it tests; prefer the code itself


def _credit(doc: _Doc, points: int, reason: str) -> None:
    """Add points and the reason for them to a file's score."""
    doc.score += points
    doc.reasons.append(reason)


# --------------------------------------------------------------- expansion

def _convention_files(paths: frozenset[str], near: list[str]) -> list[str]:
    """Convention/build files at the repo root and in every folder above the given files."""
    folders = {""}
    for p in near:
        parts = PurePosixPath(p).parent.parts
        folders.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    def folder_of(p: str) -> str:
        parent = PurePosixPath(p).parent.as_posix()
        return "" if parent == "." else parent

    found = [p for p in paths if PurePosixPath(p).name.lower() in _CONVENTION_FILES and folder_of(p) in folders]
    return sorted(found, key=lambda p: (p.count("/"), p))


def _users_of(path: str, exclude: Iterable[str], docs: dict[str, _Doc], paths: frozenset[str]) -> list[str]:
    """Non-test files that include/import ``path`` (its callers), smallest first.

    Tests are left out: a seed's own tests come from ``Language.related``.
    """
    skip = set(exclude)
    users = []
    for d in docs.values():
        if d.path in skip or d.language is None or d.content is None or _is_test(d.path):
            continue
        if path in d.language.imports(d.path, d.content, paths):
            users.append(d.path)
    return sorted(users, key=lambda p: (docs[p].file.size, p))


def _is_test(path: str) -> bool:
    """True for unit-test files and test folders (``test_x.c``, ``x_test.py``, ``tests/...``)."""
    p = PurePosixPath(path.lower())
    return (p.name.startswith(("test_", "tst_")) or p.stem.endswith(("_test", "_tests"))
            or any(part in {"test", "tests"} for part in p.parts[:-1]))


def _style_siblings(seed: _Doc, docs: dict[str, _Doc], planned: set[str]) -> list[str]:
    """Small files in the seed's folder and language: examples of the local style."""
    if seed.language is None:
        return []
    folder = PurePosixPath(seed.path).parent
    sibs = [d for d in docs.values() if d.path not in planned and d.language is not None
            and d.language.name == seed.language.name and PurePosixPath(d.path).parent == folder
            and d.content is not None]
    return [d.path for d in sorted(sibs, key=lambda d: (d.file.size, d.path))]


# ------------------------------------------------------------------ budget

def _fill_budget(plan: list[tuple[str, str, int]], docs: dict[str, _Doc], terms: TicketTerms, *,
                 max_context_bytes: int, max_file_bytes: int,
                 ) -> tuple[dict[str, str], dict[str, str], list[ContextFile]]:
    """Add planned files in tier order while they fit; relevant oversized files become excerpts."""
    files: dict[str, str] = {}
    excerpts: dict[str, str] = {}
    context: list[ContextFile] = []
    used = 0
    skipped: list[str] = []
    for path, reason, tier in sorted(plan, key=lambda item: item[2]):  # stable: keeps order within a tier
        content = docs[path].content
        if content is None:
            skipped.append(path)
            continue
        size = len(content.encode("utf-8"))
        if size <= max_file_bytes and used + size <= max_context_bytes:
            files[path] = content
            context.append(ContextFile(path, reason))
            used += size
        elif tier <= 3 and docs[path].language is not None:
            excerpt = make_excerpt(content, terms)
            if used + len(excerpt.encode("utf-8")) <= max_context_bytes:
                excerpts[path] = excerpt
                context.append(ContextFile(path, reason, excerpt=True))
                used += len(excerpt.encode("utf-8"))
            else:
                skipped.append(path)
        else:
            skipped.append(path)
    if skipped:
        log.info("context budget reached", extra={"skipped": skipped[:20], "used_bytes": used})
    return files, excerpts, context


def make_excerpt(content: str, terms: TicketTerms, *, max_bytes: int = EXCERPT_BYTES) -> str:
    """Return the most relevant parts of a large file, with omitted ranges marked.

    Keeps the first ``EXCERPT_HEAD_LINES`` lines, then windows around lines
    that mention a ticket identifier, then short windows around keyword lines,
    while the size budget allows. Gaps are shown as
    ``... [lines 40-118 omitted] ...`` so the model knows the file is partial.
    """
    lines = content.splitlines()
    windows: list[tuple[int, int]] = [(0, min(EXCERPT_HEAD_LINES, len(lines)) - 1)]
    ident_hits, keyword_hits = [], []
    for i, line in enumerate(lines):
        tokens = set(_IDENT.findall(line))
        if tokens & terms.identifiers:
            ident_hits.append(i)
        elif terms.keywords & {w for t in tokens for w in _subwords(t)}:
            keyword_hits.append(i)
    windows += [(max(0, i - EXCERPT_WINDOW), min(len(lines) - 1, i + EXCERPT_WINDOW)) for i in ident_hits]
    windows += [(max(0, i - 2), min(len(lines) - 1, i + 2)) for i in keyword_hits]
    kept: set[int] = set()
    size = 0
    for start, end in windows:  # priority order: head, identifier windows, keyword windows
        new = [i for i in range(start, end + 1) if i not in kept]
        cost = sum(len(lines[i]) + 1 for i in new)
        if size + cost > max_bytes and kept:
            continue
        kept.update(new)
        size += cost
    out: list[str] = []
    previous = -1
    for i in sorted(kept):
        if i > previous + 1:
            out.append(f"... [lines {previous + 2}-{i} omitted] ...")
        out.append(lines[i])
        previous = i
    if previous < len(lines) - 1:
        out.append(f"... [lines {previous + 2}-{len(lines)} omitted] ...")
    return "\n".join(out)


def _eligible(f: RepoFile) -> bool:
    """True for text files worth showing the LLM (by name/extension), excluding symlinks."""
    return f.mode != _SYMLINK_MODE and _is_text(f.path)


def _is_text(path: str) -> bool:
    """Return True if the path looks like a text file worth showing the LLM."""
    p = PurePosixPath(path)
    return p.name.lower() in _CONVENTION_FILES or p.suffix.lower() in _TEXT_SUFFIXES


def _rank(path: str) -> int:
    """Priority of a file for the fallback context (lower = more important).

    0 = top-level convention file (README, pyproject.toml, ...),
    1 = source code, 2 = any other text file.
    """
    p = PurePosixPath(path)
    if p.name.lower() in _CONVENTION_FILES and len(p.parts) == 1:
        return 0
    if p.suffix.lower() in _SOURCE_SUFFIXES:
        return 1
    return 2

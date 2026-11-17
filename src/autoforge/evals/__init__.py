"""Evaluation harness: measures AutoForge's output quality on a fixed suite of sample tickets.

The suite (``evals/`` at the repository root) holds small frozen fixture
repositories and cases. Each case is a ticket plus what a good change looks
like: files the context must include, files the change must and must not
touch, hidden acceptance tests (never shown to the agents) and a reference
solution that proves the case is solvable.

The harness runs the *production* agent graph (``workflow.graph``) on an
in-memory copy of a fixture repository, so nothing reaches GitHub, and grades
the final change. Variants switch parts of the loop off to measure what they
add, e.g. ``full`` (coder + checks + reviewer) against ``no-review``.

Modules: ``suite`` (load cases), ``local_repo`` (``RepoHost`` on a folder),
``grading`` (scores), ``runner`` (variants, runs, validation), ``report``
(JSON records, Markdown summary, comparison).

Entry point: ``autoforge eval context | validate | run | compare`` (``app.cli``).
"""

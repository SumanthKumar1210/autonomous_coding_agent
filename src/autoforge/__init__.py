"""AutoForge: an agentic AI coding assistant for automotive embedded software.

AutoForge turns a ticket (today a GitHub issue) into a reviewed pull request:
it reads the ticket and the repository, asks an LLM "coder" agent for a code
change, checks that change against safety rules, commits it to its own branch
and opens a PR. A human engineer always reviews and merges.

Package map (see ARCHITECTURE.md for diagrams):

- ``domain/``        pure data models and safety rules (no I/O)
- ``ports.py``       interfaces the workflow depends on
- ``agents/``        LLM-backed roles (coder) and the context they read
- ``workflow/``      orchestration of one ticket run
- ``integrations/``  adapters to external systems (GitHub)
- ``app/``           configuration, wiring and the command-line entry point
- ``observability.py`` structured logging with a per-run ID
"""

__version__ = "0.1.0"

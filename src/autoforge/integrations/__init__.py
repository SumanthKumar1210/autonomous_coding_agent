"""Integrations: concrete adapters for external systems.

Each adapter implements a port from ``autoforge.ports``:

- ``github/issues.py`` -> ``TicketSource`` (GitHub Issues as tickets)
- ``github/repo.py``   -> ``RepoHost``     (GitHub repository: read code, commit, open PRs)

Later: ``jira/`` (another ``TicketSource``) and ``sandbox/`` (a ``Verifier``).
"""

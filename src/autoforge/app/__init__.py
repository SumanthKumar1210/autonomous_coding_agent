"""Application layer: configuration, dependency wiring and entry points.

- ``config.py`` reads settings from environment variables / ``.env``.
- ``wiring.py`` builds the concrete GitHub + OpenAI objects (the only place that does).
- ``cli.py``    the ``autoforge run <issue>`` command.

The webhook service (Phase 1, increment 3) will be a second entry point here,
reusing ``wiring.py``.
"""

"""Agents: the LLM-backed roles and the context they work from.

- ``context.py``  decides which repository files the agents get to see.
- ``prompts.py``  holds the system prompts and builds the per-ticket inputs.
- ``llm.py``      the shared OpenAI structured-output call and its error handling.
- ``coder.py``    returns a structured ``CodeChange`` (revising it on feedback).
- ``reviewer.py`` returns a structured ``ReviewVerdict`` on a proposed change.
"""

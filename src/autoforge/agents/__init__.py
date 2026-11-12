"""Agents: the LLM-backed roles and the context they work from.

- ``context.py`` decides which repository files the agent gets to see.
- ``prompts.py`` holds the system prompt and builds the per-ticket input.
- ``coder.py``   calls OpenAI and returns a structured ``CodeChange``.

The reviewer/critic agent (Phase 1, increment 2) will live here too.
"""

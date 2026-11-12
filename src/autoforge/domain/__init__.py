"""Domain layer: the pure core of AutoForge.

Contains the data types every other layer talks in (``models``), the safety
rules every change must pass (``policy``) and the error types (``errors``).

Rule: nothing in here performs I/O or imports an SDK, framework or another
AutoForge layer. That keeps the core easy to test and to reason about.
"""

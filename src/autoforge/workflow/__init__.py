"""Workflow: orchestration of one ticket run.

- ``graph.py``    the LangGraph agent graph: context -> coder <-> reviewer loop
  -> optional human approval -> publish. Checkpointed after every step.
- ``pipeline.py`` the entry points ``run_ticket`` / ``resume_run``: idempotency,
  running the graph, and reporting every outcome back on the ticket.

The workflow depends only on ``domain``, ``ports``, LangGraph and the agents'
deterministic context builder, never on concrete integrations or LLM clients,
so it is tested end to end with fakes.
"""

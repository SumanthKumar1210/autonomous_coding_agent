"""Workflow: orchestration of one ticket run.

``pipeline.run_ticket`` drives the steps (context -> code -> policy -> publish
-> PR -> feedback). It depends only on ``domain``, ``ports`` and the agents'
context builder, never on concrete integrations, so it can be tested with
fakes. In Phase 1, increment 2, a LangGraph graph replaces the linear pipeline
behind the same entry point.
"""

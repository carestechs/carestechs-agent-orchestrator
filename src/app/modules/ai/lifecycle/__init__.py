"""Deterministic-flow lifecycle services (FEAT-006).

Each submodule owns transitions for one entity in the work-item / task
state machines.  Illegal transitions raise :class:`ConflictError`; the
global exception handler converts those into ``409`` Problem Details.
"""

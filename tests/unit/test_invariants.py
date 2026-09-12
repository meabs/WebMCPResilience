import pytest

from webmcp_resilience.engine.invariants import InvariantError, check


def test_compares_observed_nested_state() -> None:
    check("app.completed <= app.accepted", {"app": {"completed": 1, "accepted": 1}})


def test_rejects_failed_observed_state() -> None:
    with pytest.raises(InvariantError):
        check("app.completed <= app.accepted", {"app": {"completed": 2, "accepted": 1}})


def test_rejects_executable_expression() -> None:
    with pytest.raises(InvariantError):
        check("__import__('os').system('bad') == 1", {})

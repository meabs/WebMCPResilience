import ast
from typing import Any


class InvariantError(AssertionError):
    pass


def validate_syntax(expression: str) -> None:
    """Validate the invariant DSL without requiring a live state value."""
    try:
        tree = ast.parse(expression, mode="eval").body
    except SyntaxError as error:
        raise InvariantError(f"invalid invariant syntax: {error.msg}") from error
    if not isinstance(tree, ast.Compare) or len(tree.ops) != 1 or len(tree.comparators) != 1:
        raise InvariantError("invariants must contain one comparison")
    for node in (tree.left, tree.comparators[0]):
        _validate_value(node)


def _validate_value(node: ast.AST) -> None:
    if isinstance(node, ast.Constant):
        return
    if isinstance(node, ast.Name):
        return
    if isinstance(node, ast.Attribute):
        _validate_value(node.value)
        return
    raise InvariantError("only state paths and literal values are allowed")


def _value(state: dict[str, Any], node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return state[node.id]
    if isinstance(node, ast.Attribute):
        value = _value(state, node.value)
        if not isinstance(value, dict):
            raise InvariantError("state paths must resolve to objects")
        return value[node.attr]
    raise InvariantError("only state paths and literal values are allowed")


def check(expression: str, state: dict[str, Any]) -> None:
    """Evaluate a deliberately tiny, non-executable invariant language."""
    validate_syntax(expression)
    tree = ast.parse(expression, mode="eval").body
    left, right, op = _value(state, tree.left), _value(state, tree.comparators[0]) , tree.ops[0]
    operations = {ast.Lt: lambda: left < right, ast.LtE: lambda: left <= right, ast.Eq: lambda: left == right,
                  ast.NotEq: lambda: left != right, ast.Gt: lambda: left > right, ast.GtE: lambda: left >= right}
    operation = operations.get(type(op))
    if operation is None or not operation():
        raise InvariantError(f"{expression} (observed: {left!r} vs {right!r})")

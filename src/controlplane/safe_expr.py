"""A restricted expression evaluator for declarative preconditions.

Preconditions and YAML-declared invariants need to be *data*, not code, so that
adding one requires no supervisor change (FR-13/FR-16). Data that gets executed
is a security problem, so this evaluates a deliberately small AST subset:
comparisons, boolean operators, arithmetic, membership, subscripting, and a
short whitelist of functions. No attribute access, no calls into user objects,
no imports, no comprehensions.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

__all__ = ["ExprError", "evaluate"]


class ExprError(ValueError):
    pass


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_FUNCS: dict[str, Any] = {
    "len": len,
    "abs": abs,
    "min": min,
    "max": max,
    "int": int,
    "float": float,
    "str": str,
    "lower": lambda s: str(s).lower(),
    "startswith": lambda s, p: str(s).startswith(p),
    "endswith": lambda s, p: str(s).endswith(p),
    "get": lambda d, k, default=None: d.get(k, default) if isinstance(d, dict) else default,
    "is_none": lambda v: v is None,
    "all_of": lambda *xs: all(xs),
    "any_of": lambda *xs: any(xs),
}


def _eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ExprError(f"unknown name {node.id!r}")
        return env[node.id]
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        val = _eval(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not val
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        raise ExprError(f"unsupported unary op {type(node.op).__name__}")
    if isinstance(node, ast.BinOp):
        fn = _BIN_OPS.get(type(node.op))
        if fn is None:
            raise ExprError(f"unsupported operator {type(node.op).__name__}")
        try:
            return fn(_eval(node.left, env), _eval(node.right, env))
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise ExprError(f"cannot apply {type(node.op).__name__}: {exc}") from exc
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            fn = _CMP_OPS.get(type(op))
            if fn is None:
                raise ExprError(f"unsupported comparison {type(op).__name__}")
            right = _eval(comparator, env)
            # A malformed argument — a string where a number belongs — makes the
            # comparison meaningless rather than false. Surface it as ExprError
            # so the caller denies the call and says why. Letting the TypeError
            # escape would take the guard itself down, which is the one failure
            # mode a supervisor must not have.
            try:
                ok = fn(left, right)
            except TypeError as exc:
                raise ExprError(
                    f"cannot compare {type(left).__name__} with {type(right).__name__}: {exc}"
                ) from exc
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.Subscript):
        target = _eval(node.value, env)
        key = _eval(node.slice, env)
        try:
            return target[key]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ExprError("only whitelisted functions may be called")
        args = [_eval(a, env) for a in node.args]
        kwargs = {k.arg: _eval(k.value, env) for k in node.keywords if k.arg}
        return _FUNCS[node.func.id](*args, **kwargs)
    if isinstance(node, ast.List | ast.Tuple):
        return [_eval(e, env) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {
            _eval(k, env): _eval(v, env)
            for k, v in zip(node.keys, node.values, strict=True)
            if k is not None
        }
    raise ExprError(f"unsupported syntax {type(node).__name__}")


def evaluate(expr: str, env: dict[str, Any]) -> Any:
    """Evaluate ``expr`` against ``env``. Raises ExprError on anything unsafe."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"cannot parse {expr!r}: {exc}") from exc
    return _eval(tree, env)

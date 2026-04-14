from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal


SUPPORTED_CALLS = {"exp", "log", "sqrt", "sigmoid", "tanh"}
REAL_OUTPUT_STATUS = "required_for_real_output"


@dataclass(frozen=True)
class CalcExpr:
    kind: str
    args: tuple["CalcExpr", ...] = ()
    value: Fraction | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, object]:
        if self.kind == "const":
            if self.value is None:
                raise ValueError("Constant nodes require a value.")
            return {
                "kind": self.kind,
                "numerator": self.value.numerator,
                "denominator": self.value.denominator,
                "text": fraction_text(self.value),
            }
        if self.kind == "var":
            return {"kind": self.kind, "name": self.name}
        return {"kind": self.kind, "args": [arg.to_dict() for arg in self.args]}


@dataclass(frozen=True)
class ParseResult:
    source_text: str
    binding_name: str | None
    expression: CalcExpr | None
    status: Literal["ok", "error"]
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "source_text": self.source_text,
            "binding_name": self.binding_name,
            "status": self.status,
            "errors": list(self.errors),
            "expression": self.expression.to_dict() if self.expression else None,
        }


@dataclass(frozen=True)
class EmlExpr:
    kind: Literal["one", "var", "eml"]
    name: str | None = None
    left: "EmlExpr | None" = None
    right: "EmlExpr | None" = None

    def to_dict(self) -> dict[str, object]:
        if self.kind == "one":
            return {"kind": "one"}
        if self.kind == "var":
            return {"kind": "var", "name": self.name}
        return {
            "kind": "eml",
            "left": self.left.to_dict() if self.left else None,
            "right": self.right.to_dict() if self.right else None,
        }


def const_expr(value: int | Fraction) -> CalcExpr:
    if isinstance(value, int):
        value = Fraction(value)
    return CalcExpr(kind="const", value=value)


def one_expr() -> CalcExpr:
    return const_expr(1)


def zero_expr() -> CalcExpr:
    return const_expr(0)


def var_expr(name: str) -> CalcExpr:
    return CalcExpr(kind="var", name=name)


def unary_expr(kind: str, arg: CalcExpr) -> CalcExpr:
    return CalcExpr(kind=kind, args=(arg,))


def binary_expr(kind: str, left: CalcExpr, right: CalcExpr) -> CalcExpr:
    return CalcExpr(kind=kind, args=(left, right))


def fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def preprocess_formula_text(text: str) -> str:
    normalized = text.strip()
    normalized = normalized.replace(":=", "=")
    if "**" not in normalized and "^" in normalized:
        normalized = normalized.replace("^", "**")
    return normalized


def parse_formula(text: str) -> ParseResult:
    source = preprocess_formula_text(text)
    if not source:
        return ParseResult(source_text=text, binding_name=None, expression=None, status="error", errors=("Formula is empty.",))

    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        return ParseResult(
            source_text=text,
            binding_name=None,
            expression=None,
            status="error",
            errors=(f"Could not parse formula: {exc.msg}.",),
        )

    if len(module.body) != 1:
        return ParseResult(
            source_text=text,
            binding_name=None,
            expression=None,
            status="error",
            errors=("Only a single assignment or expression is supported in v1.",),
        )

    binding_name: str | None = None
    statement = module.body[0]
    node: ast.AST

    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            return ParseResult(
                source_text=text,
                binding_name=None,
                expression=None,
                status="error",
                errors=("Only simple `name = expression` bindings are supported in v1.",),
            )
        binding_name = statement.targets[0].id
        node = statement.value
    elif isinstance(statement, ast.Expr):
        node = statement.value
    else:
        return ParseResult(
            source_text=text,
            binding_name=None,
            expression=None,
            status="error",
            errors=("Only assignments and standalone expressions are supported in v1.",),
        )

    try:
        expression = expr_from_python_ast(node)
    except ValueError as exc:
        return ParseResult(
            source_text=text,
            binding_name=binding_name,
            expression=None,
            status="error",
            errors=(str(exc),),
        )

    return ParseResult(source_text=text, binding_name=binding_name, expression=expression, status="ok")


def expr_from_python_ast(node: ast.AST) -> CalcExpr:
    if isinstance(node, ast.Name):
        return var_expr(node.id)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            raise ValueError("Boolean constants are out of scope for CalcLang v1.")
        if isinstance(node.value, int):
            return const_expr(node.value)
        if isinstance(node.value, float):
            if not float(node.value).is_integer():
                return const_expr(Fraction(node.value).limit_denominator())
            return const_expr(int(node.value))
        raise ValueError(f"Unsupported literal type: {type(node.value).__name__}.")

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return unary_expr("neg", expr_from_python_ast(node.operand))
        if isinstance(node.op, ast.UAdd):
            return expr_from_python_ast(node.operand)
        raise ValueError("Only unary plus and unary minus are supported in v1.")

    if isinstance(node, ast.BinOp):
        left = expr_from_python_ast(node.left)
        right = expr_from_python_ast(node.right)
        if isinstance(node.op, ast.Add):
            return binary_expr("add", left, right)
        if isinstance(node.op, ast.Sub):
            return binary_expr("sub", left, right)
        if isinstance(node.op, ast.Mult):
            return binary_expr("mul", left, right)
        if isinstance(node.op, ast.Div):
            return binary_expr("div", left, right)
        if isinstance(node.op, ast.Pow):
            return binary_expr("pow", left, right)
        raise ValueError("Only +, -, *, /, and ** are supported in v1.")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function names are supported in v1.")
        function_name = node.func.id
        if function_name not in SUPPORTED_CALLS:
            raise ValueError(
                "Unsupported function call. v1 supports exp, log, sqrt, sigmoid, and tanh."
            )
        if len(node.args) != 1 or node.keywords:
            raise ValueError(f"{function_name} expects a single positional argument in v1.")
        return unary_expr(function_name, expr_from_python_ast(node.args[0]))

    raise ValueError(f"Unsupported syntax node: {type(node).__name__}.")


def normalize_expression(expression: CalcExpr) -> CalcExpr:
    return _normalize(desugar_expression(expression))


def desugar_expression(expression: CalcExpr) -> CalcExpr:
    if expression.kind in {"const", "var"}:
        return expression

    if expression.kind == "sigmoid":
        inner = desugar_expression(expression.args[0])
        return _normalize(
            binary_expr(
                "div",
                one_expr(),
                binary_expr("add", one_expr(), unary_expr("exp", unary_expr("neg", inner))),
            )
        )

    if expression.kind == "tanh":
        inner = desugar_expression(expression.args[0])
        pos = unary_expr("exp", inner)
        neg = unary_expr("exp", unary_expr("neg", inner))
        return _normalize(binary_expr("div", binary_expr("sub", pos, neg), binary_expr("add", pos, neg)))

    return CalcExpr(expression.kind, tuple(desugar_expression(arg) for arg in expression.args), expression.value, expression.name)


def _normalize(expression: CalcExpr) -> CalcExpr:
    if expression.kind in {"const", "var"}:
        return expression

    args = tuple(_normalize(arg) for arg in expression.args)

    if expression.kind == "neg":
        child = args[0]
        if is_const(child):
            return const_expr(-child.value)
        if child.kind == "neg":
            return child.args[0]
        return unary_expr("neg", child)

    if expression.kind == "pow":
        base, exponent = args
        if is_const(exponent):
            if exponent.value == 0:
                return one_expr()
            if exponent.value == 1:
                return base
            if is_const(base) and exponent.value.denominator == 1 and exponent.value >= 0:
                return const_expr(base.value ** exponent.value.numerator)
        return binary_expr("pow", base, exponent)

    if expression.kind in {"add", "mul"}:
        flat_args = flatten_args(expression.kind, args)
        const_part = Fraction(0 if expression.kind == "add" else 1)
        non_consts: list[CalcExpr] = []
        for arg in flat_args:
            if is_const(arg):
                if expression.kind == "add":
                    const_part += arg.value
                else:
                    const_part *= arg.value
            else:
                non_consts.append(arg)

        if expression.kind == "mul" and const_part == 0:
            return zero_expr()
        if expression.kind == "add" and const_part != 0:
            non_consts.append(const_expr(const_part))
        if expression.kind == "mul" and const_part != 1:
            non_consts.append(const_expr(const_part))

        if expression.kind == "add" and not non_consts:
            return zero_expr()
        if expression.kind == "mul" and not non_consts:
            return one_expr()

        if expression.kind == "mul":
            non_consts = [item for item in non_consts if not (is_const(item) and item.value == 1)]
            if not non_consts:
                return one_expr()

        if len(non_consts) == 1:
            return non_consts[0]

        ordered = tuple(sorted(non_consts, key=render_calc_expr))
        result = ordered[0]
        for item in ordered[1:]:
            result = binary_expr(expression.kind, result, item)
        return result

    if expression.kind == "sub":
        left, right = args
        if is_const(left) and is_const(right):
            return const_expr(left.value - right.value)
        if is_zero(right):
            return left
        if is_zero(left):
            return _normalize(unary_expr("neg", right))
        return binary_expr("sub", left, right)

    if expression.kind == "div":
        left, right = args
        if is_const(left) and is_const(right) and right.value != 0:
            return const_expr(left.value / right.value)
        if is_zero(left):
            return zero_expr()
        if is_one(right):
            return left
        return binary_expr("div", left, right)

    if expression.kind in {"exp", "log", "sqrt", "sigmoid", "tanh"}:
        return unary_expr(expression.kind, args[0])

    return CalcExpr(expression.kind, args, expression.value, expression.name)


def flatten_args(kind: str, args: tuple[CalcExpr, ...]) -> list[CalcExpr]:
    flat: list[CalcExpr] = []
    for arg in args:
        if arg.kind == kind:
            flat.extend(flatten_args(kind, arg.args))
        else:
            flat.append(arg)
    return flat


def is_const(expression: CalcExpr) -> bool:
    return expression.kind == "const" and expression.value is not None


def is_one(expression: CalcExpr) -> bool:
    return is_const(expression) and expression.value == 1


def is_zero(expression: CalcExpr) -> bool:
    return is_const(expression) and expression.value == 0


def render_calc_expr(expression: CalcExpr) -> str:
    return _render_calc_expr(expression, parent_precedence=-1)


def _render_calc_expr(expression: CalcExpr, parent_precedence: int) -> str:
    if expression.kind == "const":
        if expression.value is None:
            raise ValueError("Constant nodes require a value.")
        return fraction_text(expression.value)
    if expression.kind == "var":
        return expression.name or "?"

    if expression.kind in {"exp", "log", "sqrt", "sigmoid", "tanh"}:
        inner = _render_calc_expr(expression.args[0], precedence_of("neg"))
        return f"{expression.kind}({inner})"

    precedence = precedence_of(expression.kind)
    if expression.kind == "neg":
        rendered = f"-{_render_calc_expr(expression.args[0], precedence)}"
    else:
        left, right = expression.args
        operator = {
            "add": " + ",
            "sub": " - ",
            "mul": " * ",
            "div": " / ",
            "pow": " ** ",
        }[expression.kind]
        bump = 1 if expression.kind in {"sub", "div", "pow"} else 0
        rendered = (
            f"{_render_calc_expr(left, precedence)}"
            f"{operator}"
            f"{_render_calc_expr(right, precedence + bump)}"
        )

    if precedence < parent_precedence:
        return f"({rendered})"
    return rendered


def precedence_of(kind: str) -> int:
    if kind in {"add", "sub"}:
        return 10
    if kind in {"mul", "div"}:
        return 20
    if kind == "pow":
        return 30
    if kind == "neg":
        return 40
    return 100


def extract_side_conditions(expression: CalcExpr) -> list[dict[str, str]]:
    conditions: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(kind: str, condition: str, status: str, source_expr: str, rationale: str) -> None:
        key = (kind, condition, status)
        if key in seen:
            return
        seen.add(key)
        conditions.append(
            {
                "kind": kind,
                "condition": condition,
                "status": status,
                "source_expr": source_expr,
                "rationale": rationale,
            }
        )

    def visit(node: CalcExpr) -> None:
        rendered = render_calc_expr(node)
        if node.kind == "log":
            target = render_calc_expr(node.args[0])
            add("domain", f"{target} != 0", "required", rendered, "Complex.log is undefined at zero.")
            add("branch", f"principal branch of Complex.log for {rendered}", "assumed", rendered, "Complex log equalities depend on the principal branch.")
            add("domain", f"{target} > 0", REAL_OUTPUT_STATUS, rendered, "Needed if the downstream claim expects a real-valued logarithm.")
            add("totalization", f"runtime must avoid {target} = 0 before evaluating {rendered}", "boundary", rendered, "Floating-point implementations may emit NaN or Inf outside the mathematical domain.")
        elif node.kind == "div":
            denominator = render_calc_expr(node.args[1])
            add("domain", f"{denominator} != 0", "required", rendered, "Division is undefined at a zero denominator.")
            add("totalization", f"runtime must avoid division by zero in {rendered}", "boundary", rendered, "Floating-point implementations may emit NaN or Inf when denominator checks are missing.")
        elif node.kind == "sqrt":
            inner = render_calc_expr(node.args[0])
            add("branch", f"principal branch of Complex.sqrt for {rendered}", "assumed", rendered, "Complex square root uses a principal-branch convention.")
            add("domain", f"{inner} >= 0", REAL_OUTPUT_STATUS, rendered, "Needed if the downstream claim expects a real-valued square root.")
            add("totalization", f"runtime must define complex or NaN behavior for {rendered}", "boundary", rendered, "ML runtimes diverge on how invalid square-root inputs are surfaced.")

        for child in node.args:
            visit(child)

    visit(expression)
    return conditions


def compile_expression(expression: CalcExpr) -> dict[str, object]:
    exact_tree = compile_exact(expression)
    exact_subexpressions: list[dict[str, object]] = []
    uncompiled_nodes: list[dict[str, object]] = []

    def walk(node: CalcExpr, path: str) -> None:
        exact = compile_exact(node)
        rendered = render_calc_expr(node)
        if exact is not None:
            exact_subexpressions.append({"path": path, "calc_expr": rendered, "eml_tree": exact.to_dict()})
            return

        if node.kind not in {"const", "var"}:
            uncompiled_nodes.append({"path": path, "calc_expr": rendered, "kind": node.kind, "reason": unsupported_reason(node)})
        for index, child in enumerate(node.args):
            walk(child, f"{path}.{index}")

    walk(expression, "root")
    if exact_tree is not None:
        status: Literal["exact", "partial", "unsupported"] = "exact"
    elif exact_subexpressions:
        status = "partial"
    else:
        status = "unsupported"

    return {
        "status": status,
        "semantics": "complex-first",
        "real_output_layer": True,
        "eml_tree": exact_tree.to_dict() if exact_tree else None,
        "exact_subexpressions": exact_subexpressions,
        "uncompiled_nodes": dedupe_records(uncompiled_nodes),
        "trace": compile_trace(expression),
    }


def compile_exact(expression: CalcExpr) -> EmlExpr | None:
    if is_one(expression):
        return EmlExpr(kind="one")
    if expression.kind == "var":
        return EmlExpr(kind="var", name=expression.name)
    if expression.kind == "exp":
        child = compile_exact(expression.args[0])
        if child is None:
            return None
        return EmlExpr(kind="eml", left=child, right=EmlExpr(kind="one"))
    return None


def unsupported_reason(expression: CalcExpr) -> str:
    messages = {
        "const": "Only the constant `1` has a direct v1 witness in the shipped EML compiler.",
        "neg": "Negation parsing is supported, but no direct EML witness is shipped in v1.",
        "add": "Addition parsing is supported, but no direct EML witness is shipped in v1.",
        "sub": "Subtraction parsing is supported, but no direct EML witness is shipped in v1.",
        "mul": "Multiplication parsing is supported, but no direct EML witness is shipped in v1.",
        "div": "Division parsing is supported, but no direct EML witness is shipped in v1.",
        "pow": "General exponentiation parsing is supported conservatively, but no direct EML witness is shipped in v1.",
        "log": "Log parsing is supported, but the current witness library does not yet produce a pure EML tree for log.",
        "sqrt": "Sqrt parsing is supported, but the current witness library does not yet produce a pure EML tree for sqrt.",
    }
    return messages.get(expression.kind, f"No direct EML witness is shipped for `{expression.kind}` in v1.")


def compile_trace(expression: CalcExpr) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []

    def visit(node: CalcExpr, path: str) -> None:
        rendered = render_calc_expr(node)
        if compile_exact(node) is not None:
            entries.append({"path": path, "calc_expr": rendered, "status": "exact"})
            return
        entries.append({"path": path, "calc_expr": rendered, "status": "unsupported", "reason": unsupported_reason(node)})
        for index, child in enumerate(node.args):
            visit(child, f"{path}.{index}")

    visit(expression, "root")
    return entries


def dedupe_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for record in records:
        key = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def eml_mermaid(expression: CalcExpr) -> str:
    exact_tree = compile_exact(expression)
    if exact_tree is not None:
        return eml_tree_mermaid(exact_tree)
    return annotated_calc_mermaid(expression)


def eml_tree_mermaid(tree: EmlExpr) -> str:
    lines = ["graph TD"]
    counter = 0

    def visit(node: EmlExpr) -> str:
        nonlocal counter
        node_id = f"n{counter}"
        counter += 1
        label = {"one": "1", "var": node.name or "?", "eml": "eml"}[node.kind]
        lines.append(f'  {node_id}["{label}"]')
        if node.left is not None:
            left_id = visit(node.left)
            lines.append(f"  {node_id} --> {left_id}")
        if node.right is not None:
            right_id = visit(node.right)
            lines.append(f"  {node_id} --> {right_id}")
        return node_id

    visit(tree)
    return "\n".join(lines)


def annotated_calc_mermaid(expression: CalcExpr) -> str:
    lines = ["graph TD"]
    counter = 0

    def visit(node: CalcExpr) -> str:
        nonlocal counter
        node_id = f"c{counter}"
        counter += 1
        exact = compile_exact(node) is not None
        label = render_calc_expr(node).replace('"', "'")
        suffix = "exact EML" if exact else "calc/unsupported"
        lines.append(f'  {node_id}["{label}\\n{suffix}"]')
        for child in node.args:
            child_id = visit(child)
            lines.append(f"  {node_id} --> {child_id}")
        return node_id

    visit(expression)
    return "\n".join(lines)


def boundary_mermaid(formula_label: str, side_conditions: list[dict[str, str]]) -> str:
    safe_formula = formula_label.replace('"', "'")
    lines = ["graph TD", '  root["Formula"]', f'  formula["{safe_formula}"]', "  root --> formula"]
    for index, condition in enumerate(side_conditions):
        condition_id = f"b{index}"
        label = f"{condition['kind']}: {condition['condition']} ({condition['status']})".replace('"', "'")
        lines.append(f'  {condition_id}["{label}"]')
        lines.append(f"  formula --> {condition_id}")
    return "\n".join(lines)


def proof_obligations(expression: CalcExpr, compile_result: dict[str, object], side_conditions: list[dict[str, str]]) -> list[str]:
    obligations = []
    if compile_result["status"] == "exact":
        obligations.append(
            f"Prove that compile({render_calc_expr(expression)}) preserves semantics under Complex.exp/Complex.log semantics."
        )
    else:
        obligations.append(
            f"No full EML proof obligation can be discharged yet because the current witness library does not cover the full normalized form `{render_calc_expr(expression)}`."
        )
    for condition in side_conditions:
        if condition["status"] in {"required", REAL_OUTPUT_STATUS}:
            obligations.append(f"Track side condition: {condition['condition']} ({condition['status']}).")
    return obligations


def generated_theorem_name(binding_name: str | None) -> str:
    raw = binding_name or "formula_target"
    safe = "".join(ch if ch.isalnum() else "_" for ch in raw)
    return safe.strip("_") or "formula_target"


def lean_expr_for_exact_calc(expression: CalcExpr) -> str:
    if is_one(expression):
        return "CalcExpr.one"
    if expression.kind == "var":
        return f'CalcExpr.var "{expression.name}"'
    if expression.kind == "exp":
        return f"CalcExpr.exp ({lean_expr_for_exact_calc(expression.args[0])})"
    raise ValueError(f"Expression `{render_calc_expr(expression)}` is outside the exact compile subset.")


def proof_file_source(binding_name: str | None, expression: CalcExpr, compile_result: dict[str, object], side_conditions: list[dict[str, str]]) -> str:
    theorem_name = generated_theorem_name(binding_name)
    side_condition_comments = "\n".join(
        f"-- - {item['kind']}: {item['condition']} [{item['status']}]"
        for item in side_conditions
    ) or "-- - none"

    if compile_result["status"] == "exact":
        target_expr = lean_expr_for_exact_calc(expression)
        target_comment = render_calc_expr(expression)
        return f"""import Mathlib

open Complex

namespace MathlibMlArchEML

def eml (x y : Complex) : Complex := Complex.exp x - Complex.log y

inductive CalcExpr where
  | one
  | var : String -> CalcExpr
  | exp : CalcExpr -> CalcExpr
deriving Repr, DecidableEq

inductive EmlExpr where
  | one
  | var : String -> EmlExpr
  | eml : EmlExpr -> EmlExpr -> EmlExpr
deriving Repr, DecidableEq

def evalCalc (env : String -> Complex) : CalcExpr -> Complex
  | .one => 1
  | .var name => env name
  | .exp arg => Complex.exp (evalCalc env arg)

def evalEml (env : String -> Complex) : EmlExpr -> Complex
  | .one => 1
  | .var name => env name
  | .eml left right => eml (evalEml env left) (evalEml env right)

def compile : CalcExpr -> EmlExpr
  | .one => .one
  | .var name => .var name
  | .exp arg => .eml (compile arg) .one

theorem compile_sound (env : String -> Complex) : forall expr, evalEml env (compile expr) = evalCalc env expr
  | .one => by
      simp [compile, evalEml, evalCalc, eml]
  | .var name => by
      simp [compile, evalEml, evalCalc]
  | .exp arg => by
      simp [compile, evalEml, evalCalc, eml, compile_sound env arg]

def {theorem_name} : CalcExpr := {target_expr}

-- Normalized formula: {target_comment}
-- Side conditions tracked for real-output or runtime boundaries:
{side_condition_comments}

example (env : String -> Complex) : evalEml env (compile {theorem_name}) = evalCalc env {theorem_name} := by
  simpa [{theorem_name}] using compile_sound env {theorem_name}

end MathlibMlArchEML
"""

    return f"""import Mathlib

-- No exact EML proof was generated for the root formula in this run.
-- Normalized formula: {render_calc_expr(expression)}
-- The current witness library only proves the scalar subset {{1, var, exp}} exactly.
-- Side conditions:
{side_condition_comments}
-- Unsupported nodes:
{json.dumps(compile_result["uncompiled_nodes"], indent=2, ensure_ascii=False)}

example : True := by
  trivial
"""


def report_sections(
    formula_text: str,
    binding_name: str | None,
    normalized_expression: CalcExpr,
    compile_result: dict[str, object],
    side_conditions: list[dict[str, str]],
    verified_in_lean: bool,
    verification_method: str,
) -> str:
    normalized_text = render_calc_expr(normalized_expression)
    exact_count = len(compile_result["exact_subexpressions"])
    unsupported_kinds = sorted({item["kind"] for item in compile_result["uncompiled_nodes"]})
    boundary_line = (
        "Current exact witness coverage is limited to the scalar subset {1, var, exp}; "
        f"remaining unsupported node kinds: {', '.join(unsupported_kinds)}."
        if unsupported_kinds
        else "Real-output and floating-point boundaries still need explicit handling."
    )
    side_condition_text = "\n".join(
        f"- `{item['kind']}`: `{item['condition']}` ({item['status']})"
        for item in side_conditions
    ) or "- None"

    verification_line = (
        f"Lean verification succeeded via `{verification_method}`."
        if verified_in_lean
        else f"Root-claim Lean verification did not succeed; latest verification path: `{verification_method}`."
    )
    formal_evidence = (
        f"- Exact compile status: `{compile_result['status']}`\n"
        f"- Verified exact subexpressions: `{exact_count}`\n"
        f"- {verification_line}"
    )

    binding_fragment = f" bound to `{binding_name}`" if binding_name else ""
    return f"""## Proposed architecture

- Extract one explicit scalar formula{binding_fragment}: `{formula_text.strip()}`
- Normalize it into CalcLang v1: `{normalized_text}`
- Track explicit side conditions before any proof or theorem-retrieval step

## Formal evidence from mathlib

{formal_evidence}

## Engineering inference built on top of formal facts

- The formula pipeline is operating under `complex-first` semantics with a separate real-output boundary layer.
- {boundary_line}
- Side conditions tracked in this bundle:
{side_condition_text}

## Gaps requiring benchmarks or papers

- Full pure-EML witness coverage for arithmetic, division, logarithm, and square-root nodes is not shipped in v1.
- This bundle does not claim floating-point safety, training stability, or empirical ML quality improvements.

## Risks

- Principal-branch and real-output assumptions can make a formally correct complex identity misleading for real-only deployments.
- Missing runtime guards for logged, divided, or square-rooted quantities can still lead to NaN or Inf behavior.
"""


def build_evidence_record(
    formula_text: str,
    binding_name: str | None,
    normalized_expression: CalcExpr,
    compile_result: dict[str, object],
    side_conditions: list[dict[str, str]],
    verified_in_lean: bool,
    verification_method: str,
) -> dict[str, object]:
    normalized_text = render_calc_expr(normalized_expression)
    unsupported_kinds = sorted({item["kind"] for item in compile_result["uncompiled_nodes"]})

    if verified_in_lean and compile_result["status"] == "exact":
        claim_label = "Formal support"
        supported_subclaim = f"Exact EML compilation preserves `{normalized_text}` under the shipped scalar witness library."
        unsupported_boundary = "Real-only and floating-point claims still require explicit side conditions and runtime checks."
    elif compile_result["status"] == "exact":
        claim_label = "No direct formal support found in mathlib"
        supported_subclaim = f"An exact EML witness exists for `{normalized_text}`, but the root Lean verification did not succeed in this run."
        unsupported_boundary = (
            "The exact root proof remains unverified because Lean did not confirm the generated scratch file via "
            f"`{verification_method}`."
        )
    elif compile_result["exact_subexpressions"]:
        claim_label = "Partial formal support"
        supported_subclaim = (
            f"Verified EML subexpressions exist inside `{normalized_text}`, but the root formula is not yet covered by the pure EML witness library."
        )
        unsupported_boundary = "Full-root proof is blocked by unsupported node kinds: " + ", ".join(unsupported_kinds or ["unknown"]) + "."
    else:
        claim_label = "No direct formal support found in mathlib"
        supported_subclaim = f"No exact shipped EML witness currently covers `{normalized_text}`."
        unsupported_boundary = "Current EML witness library is limited to the scalar subset {1, var, exp}; unsupported node kinds: " + ", ".join(unsupported_kinds or ["unknown"]) + "."

    return {
        "name": binding_name or normalized_text,
        "import_path": "generated::eml_verify",
        "plain_language_meaning": f"EML normalization audit for `{formula_text.strip()}`.",
        "supported_subclaim": supported_subclaim,
        "unsupported_boundary": unsupported_boundary,
        "claim_label": claim_label,
        "verified_in_lean": verified_in_lean,
        "verification_method": verification_method,
        "side_conditions": side_conditions,
        "normalized_formula": normalized_text,
        "compile_status": compile_result["status"],
    }


def ensure_bundle_layout(output_dir: Path) -> dict[str, Path]:
    artifacts_dir = output_dir / "artifacts"
    figures_dir = output_dir / "figures"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    return {
        "output_dir": output_dir,
        "artifacts_dir": artifacts_dir,
        "figures_dir": figures_dir,
        "formula_json": artifacts_dir / "formula.json",
        "eml_json": artifacts_dir / "eml.json",
        "report_md": output_dir / "report.md",
        "evidence_json": output_dir / "evidence.json",
        "session_log_json": output_dir / "session_log.json",
        "eml_tree_mmd": figures_dir / "eml_tree.mmd",
        "boundary_graph_mmd": figures_dir / "boundary_graph.mmd",
    }


def load_existing_evidence(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
    return []


def replace_record(records: list[dict[str, object]], record: dict[str, object]) -> list[dict[str, object]]:
    filtered = [item for item in records if item.get("name") != record.get("name")]
    filtered.append(record)
    return filtered


def analyze_formula(text: str) -> dict[str, object]:
    parsed = parse_formula(text)
    if parsed.status != "ok" or parsed.expression is None:
        return {
            "parse": parsed.to_dict(),
            "normalized_expression": None,
            "normalized_text": None,
            "side_conditions": [],
            "compile": {"status": "unsupported", "semantics": "complex-first", "real_output_layer": True, "eml_tree": None, "exact_subexpressions": [], "uncompiled_nodes": [], "trace": []},
            "proof_obligations": [],
        }

    normalized = normalize_expression(parsed.expression)
    side_conditions = extract_side_conditions(normalized)
    compile_result = compile_expression(normalized)
    return {
        "parse": parsed.to_dict(),
        "normalized_expression": normalized.to_dict(),
        "normalized_text": render_calc_expr(normalized),
        "side_conditions": side_conditions,
        "compile": compile_result,
        "proof_obligations": proof_obligations(normalized, compile_result, side_conditions),
        "binding_name": parsed.binding_name,
        "expression_obj": normalized,
    }

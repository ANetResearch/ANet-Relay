"""Language rules of onboard programs (06 §2.4): an AST whitelist, the PARAMS literal and the rewrite
that bounds the operators which could allocate without limit.

Allowed: function definitions without decorators, assignments (``=``, augmented, annotated, ``:=``),
``if / for / while / break / continue / pass / return``, ``try / except Exception``, expressions,
comprehensions, ``lambda``, f-strings, ``assert`` and ``import math`` / ``from math import ...`` (the only
import). Everything else is refused: other imports, ``class``, ``global / nonlocal``, ``with``,
``yield / async / await``, ``raise``, ``del``, ``match``, ``try / finally``, bare or other ``except``
clauses, any name or attribute starting with ``_``, the introspection builtins (``exec eval compile open
getattr setattr delattr globals locals vars dir type __import__ input breakpoint``), the frame and code
attributes of generators and tracebacks (``gi_* cr_* ag_* f_* tb_* co_*``), ``str.format`` /
``format_map`` (their format specs reach attributes) and names that are neither defined in the program nor
part of the ``relay`` library or the builtin whitelist. Source is at most 64 KB and nested at most
``MAX_DEPTH`` levels.

``validate(source)`` never raises: it returns every problem with line and column (1-based, as editors
show them) and the parsed ``PARAMS`` / ``PARAM_RANGES``. ``compile_program(source)`` validates, rewrites
``* ** << + %`` (also augmented) into ``__relay_op__`` calls and returns a code object; it raises
``ProgramError`` with the first problem.
"""

from __future__ import annotations

import ast
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .lib import OP_NAME, known_names

MAX_SOURCE_BYTES = 64 * 1024
MAX_DEPTH = 64
MAX_PARAMS = 32
PARAM_NAME_MAX = 32
PARAM_STR_MAX = 32
FORMAT_WIDTH_DIGITS = 4
FILENAME = "<program>"

FORBIDDEN_NAMES = frozenset(
    [
        "exec",
        "eval",
        "compile",
        "open",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "type",
        "__import__",
        "input",
        "breakpoint",
        "help",
        "exit",
        "quit",
        "memoryview",
        "object",
        "super",
        "classmethod",
        "staticmethod",
        "property",
    ]
)
FORBIDDEN_ATTRS = frozenset(
    ["format", "format_map", "mro", "to_bytes", "ljust", "rjust", "center", "zfill", "expandtabs"]
)
FORBIDDEN_ATTR_PREFIXES = ("_", "gi_", "cr_", "ag_", "f_", "tb_", "co_", "func_", "im_")

_STATEMENTS: tuple[type, ...] = (
    ast.FunctionDef,
    ast.Return,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.For,
    ast.While,
    ast.If,
    ast.Break,
    ast.Continue,
    ast.Pass,
    ast.Expr,
    ast.Try,
    ast.Assert,
    ast.Import,
    ast.ImportFrom,
)
_EXPRESSIONS: tuple[type, ...] = (
    ast.BoolOp,
    ast.NamedExpr,
    ast.BinOp,
    ast.UnaryOp,
    ast.Lambda,
    ast.IfExp,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Compare,
    ast.Call,
    ast.FormattedValue,
    ast.JoinedStr,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.Starred,
    ast.Name,
    ast.List,
    ast.Tuple,
    ast.Slice,
)
_OTHER: tuple[type, ...] = (
    ast.Module,
    ast.Load,
    ast.Store,
    ast.boolop,
    ast.operator,
    ast.unaryop,
    ast.cmpop,
    ast.comprehension,
    ast.ExceptHandler,
    ast.arguments,
    ast.arg,
    ast.keyword,
    ast.alias,
)
ALLOWED = _STATEMENTS + _EXPRESSIONS + _OTHER

# Readable names for refused constructs: (English, Chinese).
_REFUSED = {
    ast.ClassDef: ("class definitions", "类定义"),
    ast.AsyncFunctionDef: ("async functions", "异步函数"),
    ast.Global: ("global statements", "global 语句"),
    ast.Nonlocal: ("nonlocal statements", "nonlocal 语句"),
    ast.With: ("with statements", "with 语句"),
    ast.AsyncWith: ("async with statements", "async with 语句"),
    ast.AsyncFor: ("async for loops", "async for 循环"),
    ast.Yield: ("yield", "yield"),
    ast.YieldFrom: ("yield from", "yield from"),
    ast.Await: ("await", "await"),
    ast.Raise: ("raise statements", "raise 语句"),
    ast.Delete: ("del statements", "del 语句"),
    ast.Match: ("match statements", "match 语句"),
    ast.TryStar: ("except* clauses", "except* 子句"),
}

_OPS = {ast.Mult: "mul", ast.Pow: "pow", ast.LShift: "lshift", ast.Add: "add", ast.Mod: "mod"}


@dataclass
class Problem:
    """One rule violation; line and col are 1-based (col counts characters)."""

    line: int
    col: int
    end_line: int
    end_col: int
    code: str
    message_en: str
    message_zh: str

    def view(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Validation:
    ok: bool
    errors: list[Problem] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    param_ranges: dict[str, list[float]] = field(default_factory=dict)
    has_init: bool = False
    has_step: bool = False

    def view(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [e.view() for e in self.errors],
            "params": self.params,
            "param_ranges": self.param_ranges,
            "has_init": self.has_init,
            "has_step": self.has_step,
        }


class ProgramError(Exception):
    """A program failed validation (``problems`` holds every error)."""

    def __init__(self, problems: list[Problem]) -> None:
        first = problems[0]
        super().__init__(f"line {first.line}: {first.message_en}")
        self.problems = problems


def _problem(node: Any, code: str, en: str, zh: str) -> Problem:
    line = getattr(node, "lineno", 1) or 1
    col = (getattr(node, "col_offset", 0) or 0) + 1
    end_line = getattr(node, "end_lineno", None) or line
    end_col = (getattr(node, "end_col_offset", None) or (col - 1)) + 1
    return Problem(line, col, end_line, max(end_col, col), code, en, zh)


def _depths(tree: ast.AST) -> int:
    """Deepest nesting of the tree, without recursion (a hostile source must not exhaust the stack)."""
    deepest = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        deepest = max(deepest, depth)
        if depth > MAX_DEPTH:
            return depth
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return deepest


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name the program binds anywhere (a superset of what is in scope at any point)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


class _Checker(ast.NodeVisitor):
    def __init__(self, known: frozenset[str]) -> None:
        self.problems: list[Problem] = []
        self.known = known

    def add(self, node: Any, code: str, en: str, zh: str) -> None:
        self.problems.append(_problem(node, code, en, zh))

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, ALLOWED):
            en, zh = _REFUSED.get(type(node), (type(node).__name__, type(node).__name__))
            self.add(node, "forbidden_syntax", f"{en} are not allowed", f"不允许使用{zh}")
            return
        super().generic_visit(node)

    # names and attributes -----------------------------------------------------

    def _name(self, node: Any, name: str) -> bool:
        if name.startswith("_"):
            self.add(
                node,
                "forbidden_name",
                f"names starting with _ are not allowed: {name}",
                f"不允许以 _ 开头的名称：{name}",
            )
            return False
        if name in FORBIDDEN_NAMES:
            self.add(node, "forbidden_name", f"{name} is not available in programs", f"程序中不可使用 {name}")
            return False
        return True

    def visit_Name(self, node: ast.Name) -> None:
        if self._name(node, node.id) and isinstance(node.ctx, ast.Load) and node.id not in self.known:
            self.add(node, "unknown_name", f"{node.id} is not defined", f"{node.id} 未定义")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr.startswith(FORBIDDEN_ATTR_PREFIXES) or attr in FORBIDDEN_ATTRS:
            self.add(
                node,
                "forbidden_attribute",
                f"attribute .{attr} is not allowed",
                f"不允许访问属性 .{attr}",
            )
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self._name(node, node.arg)
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg is not None and node.arg.startswith("_"):
            self._name(node, node.arg)
        self.generic_visit(node)

    # statements -----------------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._name(node, node.name)
        if node.decorator_list:
            self.add(
                node.decorator_list[0], "forbidden_syntax", "decorators are not allowed", "不允许使用装饰器"
            )
        if getattr(node, "type_params", None):
            self.add(node, "forbidden_syntax", "type parameters are not allowed", "不允许使用类型参数")
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name != "math":
                self.add(
                    node,
                    "forbidden_import",
                    f"only math can be imported, not {alias.name}",
                    f"只能导入 math，不能导入 {alias.name}",
                )
            elif alias.asname:
                self._name(node, alias.asname)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module != "math" or node.level:
            self.add(
                node,
                "forbidden_import",
                f"only math can be imported, not {node.module or '.'}",
                f"只能导入 math，不能导入 {node.module or '.'}",
            )
            return
        for alias in node.names:
            if alias.name == "*":
                self.add(
                    node, "forbidden_import", "from math import * is not allowed", "不允许 from math import *"
                )
            else:
                self._name(node, alias.name)
                if alias.asname:
                    self._name(node, alias.asname)

    def visit_Try(self, node: ast.Try) -> None:
        if node.finalbody:
            self.add(node, "forbidden_syntax", "try / finally is not allowed", "不允许使用 try / finally")
        for handler in node.handlers:
            if not (isinstance(handler.type, ast.Name) and handler.type.id == "Exception"):
                self.add(
                    handler,
                    "forbidden_except",
                    "only 'except Exception' is allowed",
                    "只允许 except Exception",
                )
            if handler.name:
                self._name(handler, handler.name)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        if node.is_async:
            self.add(
                node.iter, "forbidden_syntax", "async comprehensions are not allowed", "不允许异步推导式"
            )
        self.generic_visit(node)

    def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
        spec = node.format_spec
        if isinstance(spec, ast.JoinedStr):
            for part in spec.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str) and _wide(part.value):
                    self.add(node, "forbidden_format", "format width is too large", "格式宽度过大")
                elif not isinstance(part, ast.Constant):
                    # f"{x:{w}}" takes its width at run time, past the width check
                    self.add(
                        node,
                        "forbidden_format",
                        "a format spec must be written out, not computed",
                        "格式说明必须直接写出，不能在运行时计算",
                    )
                    break
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, bytes):
            self.add(node, "forbidden_syntax", "bytes literals are not allowed", "不允许使用字节串字面量")


def _wide(text: str) -> bool:
    run = 0
    for ch in text:
        run = run + 1 if ch.isdigit() else 0
        if run > FORMAT_WIDTH_DIGITS:
            return True
    return False


def _literal(node: ast.AST) -> Any:
    return ast.literal_eval(node)


def _check_params(tree: ast.Module, out: Validation) -> None:
    targets: dict[str, list[ast.AST]] = {"PARAMS": [], "PARAM_RANGES": []}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            names = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in names:
                if isinstance(target, ast.Name) and target.id in targets:
                    targets[target.id].append(node)
    for name, nodes in targets.items():
        if not nodes:
            continue
        node = nodes[0]
        top = node in tree.body
        if len(nodes) > 1 or not top or not isinstance(node, ast.Assign) or node.value is None:
            out.errors.append(
                _problem(
                    node,
                    "params_literal",
                    f"{name} must be assigned once, at the top level",
                    f"{name} 只能在顶层赋值一次",
                )
            )
            continue
        try:
            value = _literal(node.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            out.errors.append(
                _problem(
                    node, "params_literal", f"{name} must be a literal dictionary", f"{name} 必须是字面量字典"
                )
            )
            continue
        if name == "PARAMS":
            problem = _params_problem(value)
            if problem:
                out.errors.append(_problem(node, "params_value", *problem))
            else:
                out.params = value
        else:
            problem = _ranges_problem(value)
            if problem:
                out.errors.append(_problem(node, "params_value", *problem))
            else:
                out.param_ranges = {k: [float(v[0]), float(v[1])] for k, v in value.items()}
    for key in out.param_ranges:
        if key not in out.params:
            out.errors.append(
                _problem(
                    targets["PARAM_RANGES"][0],
                    "params_value",
                    f"PARAM_RANGES names {key}, which is not in PARAMS",
                    f"PARAM_RANGES 中的 {key} 不在 PARAMS 中",
                )
            )


def _params_problem(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return ("PARAMS must be a dictionary", "PARAMS 必须是字典")
    if len(value) > MAX_PARAMS:
        return (f"PARAMS has at most {MAX_PARAMS} entries", f"PARAMS 最多 {MAX_PARAMS} 项")
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not key.isidentifier()
            or key.startswith("_")
            or len(key) > PARAM_NAME_MAX
        ):
            return (f"parameter name {key!r} is not valid", f"参数名 {key!r} 无效")
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            if not math.isfinite(float(item)):
                return (f"parameter {key} must be finite", f"参数 {key} 必须是有限数")
            continue
        if isinstance(item, str):
            if len(item) > PARAM_STR_MAX:
                return (
                    f"parameter {key} is longer than {PARAM_STR_MAX} characters",
                    f"参数 {key} 超过 {PARAM_STR_MAX} 个字符",
                )
            continue
        return (
            f"parameter {key} must be a number, a boolean or a short string",
            f"参数 {key} 须为数值、布尔或短字符串",
        )
    return None


def _ranges_problem(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return ("PARAM_RANGES must be a dictionary", "PARAM_RANGES 必须是字典")
    for key, item in value.items():
        ok = (
            isinstance(key, str)
            and isinstance(item, (list, tuple))
            and len(item) == 2
            and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in item
            )
            and item[0] < item[1]
        )
        if not ok:
            return (f"PARAM_RANGES[{key!r}] must be [low, high]", f"PARAM_RANGES[{key!r}] 须为 [下限, 上限]")
    return None


def validate(source: Any) -> Validation:
    """Check a program against the language rules; never raises."""
    out = Validation(ok=False)
    if not isinstance(source, str):
        out.errors.append(Problem(1, 1, 1, 1, "invalid_source", "source must be text", "源码必须是文本"))
        return out
    if len(source.encode("utf-8", "surrogatepass")) > MAX_SOURCE_BYTES:
        out.errors.append(Problem(1, 1, 1, 1, "too_large", "source is larger than 64 KB", "源码超过 64 KB"))
        return out
    try:
        tree = ast.parse(source, FILENAME, "exec")
    except SyntaxError as exc:
        line = exc.lineno or 1
        col = exc.offset or 1
        out.errors.append(
            Problem(
                line,
                col,
                exc.end_lineno or line,
                max(exc.end_offset or col, col),
                "syntax",
                f"syntax error: {exc.msg}",
                f"语法错误：{exc.msg}",
            )
        )
        return out
    except (ValueError, MemoryError, RecursionError) as exc:
        out.errors.append(
            Problem(1, 1, 1, 1, "syntax", f"source cannot be parsed: {exc}", f"源码无法解析：{exc}")
        )
        return out
    if _depths(tree) > MAX_DEPTH:
        out.errors.append(
            Problem(
                1,
                1,
                1,
                1,
                "too_deep",
                f"code is nested deeper than {MAX_DEPTH} levels",
                f"代码嵌套超过 {MAX_DEPTH} 层",
            )
        )
        return out
    known = known_names() | frozenset(_bound_names(tree))
    checker = _Checker(known)
    checker.visit(tree)
    out.errors.extend(checker.problems)
    _check_params(tree, out)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.name == "step":
                out.has_step = True
                if len(node.args.args) + len(node.args.posonlyargs) < 2 and node.args.vararg is None:
                    out.errors.append(
                        _problem(
                            node,
                            "step_signature",
                            "step must take (obs, ctx)",
                            "step 须接受 (obs, ctx) 两个参数",
                        )
                    )
            elif node.name == "init":
                out.has_init = True
    if not out.has_step:
        out.errors.append(
            Problem(
                1, 1, 1, 1, "no_step", "the program defines no step(obs, ctx)", "程序未定义 step(obs, ctx)"
            )
        )
    out.errors.sort(key=lambda p: (p.line, p.col))
    out.ok = not out.errors
    return out


class _Rewrite(ast.NodeTransformer):
    """``a * b`` -> ``__relay_op__("mul", a, b)`` (and ``**``, ``<<``, ``+``, ``%``, augmented too)."""

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        op = _OPS.get(type(node.op))
        if op is None:
            return node
        call = ast.Call(
            func=ast.Name(id=OP_NAME, ctx=ast.Load()),
            args=[ast.Constant(op), node.left, node.right],
            keywords=[],
        )
        return ast.copy_location(call, node)

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.AST:
        self.generic_visit(node)
        op = _OPS.get(type(node.op))
        if op is None:
            return node
        load = _as_load(node.target)
        call = ast.Call(
            func=ast.Name(id=OP_NAME, ctx=ast.Load()), args=[ast.Constant(op), load, node.value], keywords=[]
        )
        assign = ast.Assign(targets=[node.target], value=ast.copy_location(call, node))
        return ast.copy_location(assign, node)


def _as_load(target: ast.AST) -> ast.AST:
    import copy

    clone = copy.deepcopy(target)
    for node in ast.walk(clone):
        if hasattr(node, "ctx"):
            node.ctx = ast.Load()
    return clone


def compile_program(source: str) -> Any:
    """Validate, rewrite and compile; raises ``ProgramError``."""
    result = validate(source)
    if not result.ok:
        raise ProgramError(result.errors)
    tree = ast.parse(source, FILENAME, "exec")
    tree = ast.fix_missing_locations(_Rewrite().visit(tree))
    return compile(tree, FILENAME, "exec", dont_inherit=True, optimize=0)


__all__ = [
    "FILENAME",
    "MAX_SOURCE_BYTES",
    "Problem",
    "ProgramError",
    "Validation",
    "compile_program",
    "validate",
]

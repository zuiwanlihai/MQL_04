"""
Static AST validator for strategy source code (M3).

Pre-flight check only — NOT a security boundary. The sandbox import hook
(backtest slice, M4) is the enforcement point; dynamic constructs such as
``getattr(pd, "read_csv")`` cannot be caught statically here.

Rule codes (authoritative list: docs/SDK-v1-API规范.md §校验规则):
    E001 语法错误
    E002 缺少 on_init        E003 缺少 on_bar        E004 缺少 on_order
    E005 回调形参数量不符（on_init=1, on_bar=2, on_order=2, on_stop=2, on_tick=2）
    I001 禁用模块 import     I002 非白名单 import    I003 import *
    I004 白名单模块的逃逸成员（numpy.load / pandas.read_csv …）
    F001 危险内建函数（eval/exec/compile/__import__/open/breakpoint）
    F002 真实时钟调用（.now()/.utcnow()/.today()）
    F003 numpy/pandas 文件入口
    W001 on_tick 回调仅在虚拟下单/实盘触发，回测不调用（info）
    W002 get_bars 显式 end_time 参数
    W003 模块顶层可执行语句
    W004 缺少可选 on_stop
"""

from __future__ import annotations

import ast

from app.schemas.strategy import ValidationIssue, ValidationResult

# 标准库白名单（PRD M3 ③，逐项授予；封闭集合）
STDLIB_WHITELIST: frozenset[str] = frozenset(
    {
        "math",
        "statistics",
        "datetime",
        "decimal",
        "collections",
        "itertools",
        "functools",
        "dataclasses",
        "enum",
        "typing",
        "json",
        "re",
        "string",
        "copy",
        "random",
    }
)

# 第三方白名单（MVP = numpy / pandas / scipy）
THIRD_PARTY_WHITELIST: frozenset[str] = frozenset({"numpy", "pandas", "scipy"})

# 显式禁止清单（PRD M3 ③ 最小要求，实现可加严）
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "socket", "ssl", "http", "urllib", "requests", "ftplib", "smtplib",
        "os", "io", "pathlib", "shutil", "tempfile", "mmap",
        "subprocess", "ctypes", "sys", "signal", "atexit",
        "threading", "multiprocessing", "asyncio", "concurrent", "queue",
        "importlib", "pickle", "marshal", "time",
    }
)

# 白名单模块的逃逸成员（第三方库自身内部依赖不受限，但对外入口必须拦）
ESCAPE_MEMBERS: dict[str, frozenset[str]] = {
    "numpy": frozenset({"load", "save", "savetxt", "loadtxt", "memmap", "fromfile"}),
    "pandas": frozenset(
        {
            "read_csv", "read_pickle", "read_parquet", "read_json", "read_html",
            "read_xml", "read_fwf", "read_sql", "read_excel", "read_stata",
            "read_feather", "read_hdf", "read_orc", "read_sas", "read_spss",
            "to_csv", "to_pickle", "to_parquet", "to_json", "to_html", "to_xml",
            "to_excel", "to_stata", "to_feather", "HDFStore", "ExcelFile",
        }
    ),
    "datetime": frozenset({"now", "utcnow", "today", "fromtimestamp", "strptime"}),
}

DANGEROUS_BUILTINS: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "__import__", "open", "breakpoint"}
)

REAL_CLOCK_METHODS: frozenset[str] = frozenset({"now", "utcnow", "today"})

REQUIRED_CALLBACKS: tuple[str, ...] = ("on_init", "on_bar", "on_order")
CALLBACK_ARITY: dict[str, int] = {
    "on_init": 1, "on_bar": 2, "on_order": 2, "on_stop": 2, "on_tick": 2,
}

_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}
_CODE_PRIORITY = {
    "I001": 0, "I004": 1, "I003": 2, "I002": 3,
    "F001": 0, "F003": 1, "F002": 2,
}


def _root_module(name: str) -> str:
    return name.split(".")[0]


class _CallbackVisitor(ast.NodeVisitor):
    """Single pass collecting all findings."""

    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []
        self.top_level_funcs: dict[str, ast.FunctionDef] = {}
        # alias -> real module, e.g. np -> numpy
        self.module_aliases: dict[str, str] = {}
        self.imported_names: dict[str, str] = {}  # local name -> qualified origin

    def _add(
        self, code: str, severity: str, node: ast.AST, message: str
    ) -> None:
        self.issues.append(
            ValidationIssue(
                code=code,
                severity=severity,  # type: ignore[arg-type]
                line=getattr(node, "lineno", 1),
                column=getattr(node, "col_offset", 0),
                message=message,
            )
        )

    # ── imports ────────────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name, node, asname=alias.asname)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        root = _root_module(mod)
        if root in FORBIDDEN_MODULES:
            self._add("I001", "error", node, f"禁止导入模块 {mod}")
        elif root not in STDLIB_WHITELIST | THIRD_PARTY_WHITELIST:
            self._add("I002", "error", node, f"模块 {mod} 不在 SDK v1 白名单内")
        for alias in node.names:
            if alias.name == "*":
                self._add("I003", "error", node, f"禁止 from {mod} import *")
            qualified = f"{mod}.{alias.name}"
            members = ESCAPE_MEMBERS.get(root, frozenset())
            if alias.name in members:
                self._add(
                    "I004", "error", node,
                    f"{qualified} 属于文件/URL/时钟逃逸入口，SDK v1 禁止使用",
                )
            self.imported_names[alias.asname or alias.name] = qualified
        self.generic_visit(node)

    def _check_module(self, name: str, node: ast.AST, asname: str | None) -> None:
        root = _root_module(name)
        if root in FORBIDDEN_MODULES:
            self._add("I001", "error", node, f"禁止导入模块 {name}")
        elif root not in STDLIB_WHITELIST | THIRD_PARTY_WHITELIST:
            self._add("I002", "error", node, f"模块 {name} 不在 SDK v1 白名单内")
        local = asname or root
        self.module_aliases[local] = root

    # ── calls ──────────────────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in DANGEROUS_BUILTINS:
                self._add("F001", "error", node, f"禁止调用 {func.id}()")
            elif func.id == "sleep":
                self._add("I001", "error", node, "沙箱不提供 sleep，无限循环由超时熔断处置")
        elif isinstance(func, ast.Attribute):
            base = _dotted(func)
            parts = base.split(".")
            root = self.module_aliases.get(parts[0], parts[0]) if parts else ""
            member = parts[-1] if len(parts) >= 2 else ""
            members = ESCAPE_MEMBERS.get(root, frozenset())
            if member in members:
                if root in ("numpy", "pandas"):
                    self._add(
                        "F003", "error", node,
                        f"{base} 是文件/URL 入口，SDK v1 禁止（用 ctx.storage 持久化）",
                    )
                else:
                    self._add(
                        "I004", "error", node,
                        f"{base} 属于时钟/文件逃逸入口，时间一律取自 ctx",
                    )
            if member in REAL_CLOCK_METHODS and len(parts) >= 2:
                origin = self.imported_names.get(parts[0], root)
                if origin == "datetime" or root == "datetime" or parts[0] == "datetime":
                    self._add(
                        "F002", "error", node,
                        f"{base}() 访问真实时钟，时间一律取自 ctx（UTC）",
                    )
        # W002: get_bars(..., end_time=...)
        if isinstance(func, ast.Attribute) and func.attr == "get_bars":
            for kw in node.keywords:
                if kw.arg == "end_time":
                    self._add(
                        "W002", "warning", node,
                        "显式 end_time 由 SDK 截断至当前 bar，回测中无意义",
                    )
        self.generic_visit(node)

    # ── module structure ───────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.col_offset == 0 and node.name in CALLBACK_ARITY:
            self.top_level_funcs[node.name] = node
            args = node.args
            count = len(args.posonlyargs) + len(args.args)
            if args.vararg:
                count += 1
            expected = CALLBACK_ARITY[node.name]
            if count != expected and not args.kwonlyargs:
                self._add(
                    "E005", "error", node,
                    f"{node.name}() 需要 {expected} 个形参 (ctx…)，当前 {count} 个",
                )
        if node.name == "on_tick":
            self._add(
                "W001", "info", node,
                "on_tick 仅在虚拟下单/实盘由真实 tick 触发，回测不调用（回测结果不含 tick 级行为）",
            )
        self.generic_visit(node)

    def _check_top_level(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            if isinstance(
                stmt,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import,
                 ast.ImportFrom, ast.Expr),
            ):
                continue
            if isinstance(stmt, ast.Assign):
                continue
            self._add(
                "W003", "warning", stmt,
                "模块顶层可执行语句将在加载时运行，建议移入 on_init",
            )


def _dotted(node: ast.Attribute) -> str:
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def validate_source(code: str) -> ValidationResult:
    """Run all AST rules; ``ok`` = no error-severity issue."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        return ValidationResult(
            ok=False,
            issues=[
                ValidationIssue(
                    code="E001",
                    severity="error",
                    line=exc.lineno or 1,
                    column=max((exc.offset or 1) - 1, 0),
                    message=f"语法错误：{exc.msg}",
                )
            ],
        )

    v = _CallbackVisitor()
    v.visit(tree)
    v._check_top_level(tree)

    for name in REQUIRED_CALLBACKS:
        if name not in v.top_level_funcs:
            v.issues.append(
                ValidationIssue(
                    code={"on_init": "E002", "on_bar": "E003", "on_order": "E004"}[name],
                    severity="error",
                    line=1,
                    column=0,
                    message=f"必须实现顶层回调 {name}()",
                )
            )
    if "on_stop" not in v.top_level_funcs:
        v.issues.append(
            ValidationIssue(
                code="W004",
                severity="info",
                line=1,
                column=0,
                message="建议实现可选回调 on_stop(ctx, reason) 供停止时收尾",
            )
        )

    # 同一节点位置去重：保留优先级最高的规则码；合成问题（缺失回调）不去重
    deduped: dict[tuple[int, int, str], ValidationIssue] = {}
    for issue in v.issues:
        key = (issue.line, issue.column, issue.code if issue.code.startswith("E00") else "")
        prev = deduped.get(key)
        if prev is None:
            deduped[key] = issue
            continue
        rank = (_SEVERITY_RANK[issue.severity], _CODE_PRIORITY.get(issue.code, 9))
        prev_rank = (_SEVERITY_RANK[prev.severity], _CODE_PRIORITY.get(prev.code, 9))
        if rank < prev_rank:
            deduped[key] = issue

    issues = sorted(deduped.values(), key=lambda i: (i.line, i.column))
    ok = not any(i.severity == "error" for i in issues)
    return ValidationResult(ok=ok, issues=issues)


def has_callback(source: str, name: str) -> bool:
    """``True`` when a top-level ``def name(...)`` exists in the source.

    Used by the paper/live deployment path to decide whether the on_tick
    tick-polling task should run.  Syntax errors → ``False``.
    """
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.FunctionDef) and node.name == name
        for node in tree.body
    )


class _NotebookVisitor(ast.NodeVisitor):
    """Lightweight AST pass for research-notebook cells.

    与策略校验的差异：不要求策略回调、不限制真实时钟（datetime.now
    在研究场景无害），但 import 白名单 / 危险 builtin / 文件与 URL
    逃逸成员的规则与策略沙箱完全一致。
    """

    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []

    def _add(self, code: str, node: ast.AST, message: str) -> None:
        self.issues.append(
            ValidationIssue(
                code=code,
                severity="error",
                line=getattr(node, "lineno", 1),
                column=getattr(node, "col_offset", 0),
                message=message,
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = _root_module(alias.name)
            if root in FORBIDDEN_MODULES:
                self._add("I001", node, f"禁止导入模块 {alias.name}")
            elif root not in STDLIB_WHITELIST | THIRD_PARTY_WHITELIST:
                self._add("I002", node, f"模块 {alias.name} 不在研究沙箱白名单内")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        root = _root_module(mod)
        if root in FORBIDDEN_MODULES:
            self._add("I001", node, f"禁止导入模块 {mod}")
        elif root not in STDLIB_WHITELIST | THIRD_PARTY_WHITELIST:
            self._add("I002", node, f"模块 {mod} 不在研究沙箱白名单内")
        members = ESCAPE_MEMBERS.get(root, frozenset())
        for alias in node.names:
            if alias.name == "*":
                self._add("I003", node, f"禁止 from {mod} import *")
            if alias.name in members:
                self._add(
                    "I004", node,
                    f"{mod}.{alias.name} 属于文件/URL 逃逸入口，研究沙箱禁止使用",
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in DANGEROUS_BUILTINS:
            self._add("F001", node, f"禁止调用 {func.id}()")
        elif isinstance(func, ast.Attribute) and func.attr in DANGEROUS_BUILTINS:
            self._add("F001", node, f"禁止调用 ...{func.attr}()")
        self.generic_visit(node)


def validate_notebook_source(code: str) -> ValidationResult:
    """AST check for a notebook cell; ``ok`` = no error-severity issue."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        return ValidationResult(
            ok=False,
            issues=[
                ValidationIssue(
                    code="E001",
                    severity="error",
                    line=exc.lineno or 1,
                    column=max((exc.offset or 1) - 1, 0),
                    message=f"语法错误：{exc.msg}",
                )
            ],
        )
    v = _NotebookVisitor()
    v.visit(tree)
    issues = sorted(v.issues, key=lambda i: (i.line, i.column))
    ok = not any(i.severity == "error" for i in issues)
    return ValidationResult(ok=ok, issues=issues)

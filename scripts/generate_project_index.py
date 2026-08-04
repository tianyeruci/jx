"""Build and verify the generated portions of the project function index.

The generator deliberately parses source files instead of importing the FastAPI
application.  Documentation checks must work without production credentials,
network access, or service startup side effects.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "docs" / "功能与接口索引.md"
MAIN_PATH = ROOT / "ai_management" / "main.py"

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})
FRAMEWORK_ROUTE_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
GENERATED_SECTIONS = ("HTTP", "CLI", "WORKERS", "COMPOSE", "DIFY", "SCRIPTS")

ROUTER_DOMAINS = {
    "ai_management.api.routers.admin": "管理与运维",
    "ai_management.api.routers.admin_logs": "管理与运维",
    "ai_management.api.routers.admin_management": "管理与运维",
    "ai_management.api.routers.business": "商业会话、SSE、隐私",
    "ai_management.api.routers.entry": "应用入口、健康、鉴权",
    "ai_management.api.routers.health": "应用入口、健康、鉴权",
    "ai_management.api.routers.knowledge_admin": "文档清洗、复核、重试",
    "ai_management.api.routers.tools": "朋友圈、写作、画像",
    "ai_management.cleaning": "文档清洗、复核、重试",
}
ROUTE_DOMAIN_OVERRIDES = {
    ("ai_management.api.routers.entry", "redirect_course"): "课程、路线、点击",
}


class IndexGenerationError(RuntimeError):
    """Raised when static source shape cannot safely be represented."""


@dataclass(frozen=True)
class Route:
    domain: str
    method: str
    path: str
    handler: str
    source: str


@dataclass(frozen=True)
class WorkerTask:
    name: str
    handler: str
    source: str


def read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def parse_python(path: Path) -> ast.Module:
    try:
        return ast.parse(read_text(path), filename=str(path))
    except SyntaxError as exc:
        raise IndexGenerationError(f"无法解析 {path.relative_to(ROOT)}：{exc.msg}") from exc


def literal_string(node: ast.AST | None, context: str) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise IndexGenerationError(f"{context} 必须是静态字符串，不能自动安全索引。")


def literal_string_sequence(node: ast.AST | None, context: str) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        raise IndexGenerationError(f"{context} 必须是静态字符串列表，不能自动安全索引。")
    return [literal_string(item, context) for item in node.elts]


def is_name(node: ast.AST, value: str) -> bool:
    return isinstance(node, ast.Name) and node.id == value


def relative_module(import_node: ast.ImportFrom) -> str:
    if import_node.level != 1 or not import_node.module:
        raise IndexGenerationError(
            "ai_management/main.py 的 Router 导入必须使用一级相对导入，"
            "以便索引器确认实际注册来源。"
        )
    return f"ai_management.{import_node.module}"


def router_imports(main_tree: ast.Module) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in ast.walk(main_tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        router_names = [imported for imported in node.names if imported.name == "router"]
        if not router_names:
            continue
        module = relative_module(node)
        for imported in router_names:
            imports[imported.asname or imported.name] = module
    return imports


def name_sequence(node: ast.AST) -> list[str] | None:
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    names: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Name):
            return None
        names.append(item.id)
    return names


def is_include_router_call(node: ast.AST, argument_name: str) -> bool:
    if not isinstance(node, ast.Call) or not node.args:
        return False
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "include_router":
        return False
    return is_name(node.args[0], argument_name)


def registered_router_aliases(main_tree: ast.Module, imports: dict[str, str]) -> list[str]:
    aliases: list[str] = []
    for node in ast.walk(main_tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            candidates = name_sequence(node.iter)
            if candidates and any(
                is_include_router_call(call, node.target.id) for call in ast.walk(node)
            ):
                aliases.extend(candidates)
        if is_include_router_call(node, ""):
            continue
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Name):
            if is_include_router_call(node, node.args[0].id) and node.args[0].id in imports:
                aliases.append(node.args[0].id)

    registered: list[str] = []
    for alias in aliases:
        if alias not in imports:
            raise IndexGenerationError(
                f"main.py 注册的 Router {alias!r} 没有对应的静态 router 导入。"
            )
        if alias not in registered:
            registered.append(alias)
    if not registered:
        raise IndexGenerationError("未能从 ai_management/main.py 找到实际注册的 Router。")
    return registered


def module_path(root: Path, module: str) -> Path:
    path = root.joinpath(*module.split(".")).with_suffix(".py")
    if not path.is_file():
        raise IndexGenerationError(f"Router 模块不存在：{path.relative_to(root)}")
    return path


def router_prefix(tree: ast.Module, source: Path) -> str:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(is_name(target, "router") for target in targets):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or not is_name(value.func, "APIRouter"):
            continue
        for keyword in value.keywords:
            if keyword.arg == "prefix":
                return literal_string(keyword.value, f"{source.relative_to(ROOT)} 的 Router prefix")
        return ""
    raise IndexGenerationError(f"{source.relative_to(ROOT)} 未找到 router = APIRouter(...)。")


def decorator_methods(decorator: ast.Call, source: Path) -> list[str] | None:
    if not isinstance(decorator.func, ast.Attribute) or not is_name(
        decorator.func.value, "router"
    ):
        return None
    operation = decorator.func.attr
    if operation in HTTP_METHODS:
        return [operation.upper()]
    if operation == "api_route":
        for keyword in decorator.keywords:
            if keyword.arg == "methods":
                return [method.upper() for method in literal_string_sequence(
                    keyword.value, f"{source.relative_to(ROOT)} 的 api_route methods"
                )]
        raise IndexGenerationError(
            f"{source.relative_to(ROOT)} 的 @router.api_route 必须声明静态 methods。"
        )
    raise IndexGenerationError(
        f"{source.relative_to(ROOT)} 的 @router.{operation} 尚未被索引器支持。"
    )


def decorator_path(decorator: ast.Call, source: Path) -> str:
    if decorator.args:
        return literal_string(decorator.args[0], f"{source.relative_to(ROOT)} 的路由路径")
    for keyword in decorator.keywords:
        if keyword.arg == "path":
            return literal_string(keyword.value, f"{source.relative_to(ROOT)} 的路由路径")
    raise IndexGenerationError(f"{source.relative_to(ROOT)} 的路由缺少静态路径。")


def join_route_path(prefix: str, path: str) -> str:
    if not prefix:
        result = path
    elif not path:
        result = prefix
    else:
        result = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
    if not result.startswith("/"):
        result = f"/{result}"
    return result or "/"


def collect_routes(root: Path = ROOT) -> list[Route]:
    main_tree = parse_python(root / "ai_management" / "main.py")
    imports = router_imports(main_tree)
    registered = registered_router_aliases(main_tree, imports)
    routes: list[Route] = []

    for alias in registered:
        module = imports[alias]
        source = module_path(root, module)
        tree = parse_python(source)
        prefix = router_prefix(tree, source)
        domain = ROUTER_DOMAINS.get(module)
        if not domain:
            raise IndexGenerationError(f"缺少 {module} 的功能域映射。")
        relative_source = source.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                methods = decorator_methods(decorator, source)
                if methods is None:
                    continue
                path = join_route_path(prefix, decorator_path(decorator, source))
                route_domain = ROUTE_DOMAIN_OVERRIDES.get((module, node.name), domain)
                for method in methods:
                    routes.append(
                        Route(
                            domain=route_domain,
                            method=method,
                            path=path,
                            handler=f"{module}.{node.name}",
                            source=f"{relative_source}:{node.lineno}",
                        )
                    )

    duplicates = {(route.method, route.path) for route in routes}
    if len(duplicates) != len(routes):
        raise IndexGenerationError("发现重复的 HTTP 方法和路径组合，不能生成含歧义的索引。")
    return sorted(routes, key=lambda route: (route.domain, route.path, route.method))


def collect_console_scripts(root: Path = ROOT) -> list[tuple[str, str]]:
    data = tomllib.loads(read_text(root / "pyproject.toml"))
    scripts = data.get("project", {}).get("scripts", {})
    if not isinstance(scripts, dict):
        raise IndexGenerationError("pyproject.toml 的 project.scripts 必须是映射。")
    return sorted((str(name), str(target)) for name, target in scripts.items())


def is_celery_task_decorator(decorator: ast.AST) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "task"
        and is_name(decorator.func.value, "celery_app")
    )


def collect_worker_tasks(root: Path = ROOT) -> list[WorkerTask]:
    task_root = root / "ai_management" / "workers" / "tasks"
    tasks: list[WorkerTask] = []
    for source in sorted(task_root.glob("*.py")):
        if source.name == "__init__.py":
            continue
        tree = parse_python(source)
        relative_source = source.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not is_celery_task_decorator(decorator):
                    continue
                name_node = next(
                    (keyword.value for keyword in decorator.keywords if keyword.arg == "name"),
                    None,
                )
                name = literal_string(
                    name_node, f"{relative_source} 的 Celery task name"
                )
                module = ".".join(source.relative_to(root).with_suffix("").parts)
                tasks.append(
                    WorkerTask(
                        name=name,
                        handler=f"{module}.{node.name}",
                        source=f"{relative_source}:{node.lineno}",
                    )
                )
    return sorted(tasks, key=lambda task: task.name)


def collect_compose_services(root: Path = ROOT) -> list[tuple[str, str, str]]:
    try:
        import yaml
    except ImportError as exc:
        raise IndexGenerationError("索引器需要项目依赖 PyYAML 来读取 Compose 入口。") from exc

    services: list[tuple[str, str, str]] = []
    for filename in ("docker-compose.ai.yml", "docker-compose.ui.yml"):
        source = root / filename
        data = yaml.safe_load(read_text(source))
        service_map = data.get("services", {}) if isinstance(data, dict) else {}
        if not isinstance(service_map, dict):
            raise IndexGenerationError(f"{filename} 的 services 必须是映射。")
        for name, config in service_map.items():
            command = config.get("command", "") if isinstance(config, dict) else ""
            if isinstance(command, list):
                command = " ".join(str(item) for item in command)
            services.append((str(name), filename, str(command)))
    return sorted(services)


def collect_dify_assets(root: Path = ROOT) -> list[tuple[str, str]]:
    assets: list[tuple[str, str]] = []
    for directory, kind in (("dify/dsl", "DSL"), ("dify/prompts", "Prompt")):
        for source in sorted((root / directory).glob("*")):
            if source.is_file():
                assets.append((kind, source.relative_to(root).as_posix()))
    return assets


def collect_operational_scripts(root: Path = ROOT) -> list[str]:
    script_root = root / "scripts"
    patterns = (
        "jx_*.sh",
        "jx_*.py",
        "dify_*.py",
        "configure_dify_*.py",
        "inspect_dify_*.py",
        "pdf_markdown_*.py",
    )
    sources = {
        source.relative_to(root).as_posix()
        for pattern in patterns
        for source in script_root.glob(pattern)
        if source.is_file()
    }
    return sorted(sources)


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(escape_cell(value) for value in row) + " |"
        for row in rows
    ]
    return "\n".join([head, separator, *body])


def render_generated_sections(root: Path = ROOT) -> dict[str, str]:
    routes = collect_routes(root)
    http_rows = [
        [
            route.domain,
            route.method,
            f"`{route.path}`",
            f"`{route.handler}`",
            f"`{route.source}`",
        ]
        for route in routes
    ]

    cli_rows = [
        [f"`{name}`", f"`{target}`", "`pyproject.toml`"]
        for name, target in collect_console_scripts(root)
    ]
    cli_rows.append(
        [
            "Streamlit 测试/运维页",
            "`streamlit run ai_management/test_ui.py`",
            "`docker-compose.ui.yml`",
        ]
    )

    worker_rows = [
        [f"`{task.name}`", f"`{task.handler}`", f"`{task.source}`"]
        for task in collect_worker_tasks(root)
    ]

    compose_rows = [
        [name, f"`{source}`", f"`{command}`"]
        for name, source, command in collect_compose_services(root)
    ]

    dify_rows = [[kind, f"`{path}`"] for kind, path in collect_dify_assets(root)]
    script_rows = [[f"`{path}`"] for path in collect_operational_scripts(root)]

    return {
        "HTTP": "### HTTP 接口（静态解析已注册 Router）\n\n"
        + markdown_table(
            ["功能域", "方法", "路径", "处理函数", "源码"],
            http_rows,
        ),
        "CLI": "### 命令与界面入口\n\n"
        + markdown_table(["入口", "目标", "定义"], cli_rows),
        "WORKERS": "### Celery 任务\n\n"
        + markdown_table(["任务名", "处理函数", "源码"], worker_rows),
        "COMPOSE": "### Compose 服务入口\n\n"
        + markdown_table(["服务", "定义", "命令"], compose_rows),
        "DIFY": "### Dify 资产\n\n"
        + markdown_table(["类型", "路径"], dify_rows),
        "SCRIPTS": "### 发布、验收与 RAG 脚本\n\n"
        + markdown_table(["脚本"], script_rows),
    }


def section_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?ms)^<!-- GENERATED:{name}:START -->\r?\n.*?^<!-- GENERATED:{name}:END -->"
    )


def document_newline(document: str) -> str:
    return "\r\n" if "\r\n" in document else "\n"


def replace_generated_sections(root: Path, document: str) -> str:
    newline = document_newline(document)
    updated = document
    for name, content in render_generated_sections(root).items():
        pattern = section_pattern(name)
        if not pattern.search(updated):
            raise IndexGenerationError(
                f"{INDEX_PATH.relative_to(root)} 缺少 GENERATED:{name} 标记。"
            )
        body = content.replace("\n", newline).rstrip("\r\n")
        replacement = (
            f"<!-- GENERATED:{name}:START -->{newline}"
            f"{body}{newline}"
            f"<!-- GENERATED:{name}:END -->"
        )
        updated = pattern.sub(replacement, updated, count=1)
    return updated


def changed_sections(root: Path, document: str) -> list[str]:
    expected = replace_generated_sections(root, document)
    changed: list[str] = []
    for name in GENERATED_SECTIONS:
        pattern = section_pattern(name)
        if pattern.search(document).group(0) != pattern.search(expected).group(0):
            changed.append(name)
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="更新索引中的自动生成区。")
    mode.add_argument("--check", action="store_true", help="检查索引是否已过期。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        current = read_text(INDEX_PATH)
        expected = replace_generated_sections(ROOT, current)
        changed = changed_sections(ROOT, current)
    except (IndexGenerationError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"功能与接口索引失败：{exc}", file=sys.stderr)
        return 2

    if args.check:
        if changed:
            print(
                "功能与接口索引已过期："
                + "、".join(changed)
                + "。请运行 python scripts/generate_project_index.py --write"
            )
            return 1
        print("功能与接口索引已是最新。")
        return 0

    if current == expected:
        print("功能与接口索引无需更新。")
        return 0
    write_text(INDEX_PATH, expected)
    print("功能与接口索引已更新：" + "、".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import os
import subprocess
import sys
from pathlib import Path

from scripts import generate_project_index

ROOT = Path(__file__).resolve().parents[1]
TROUBLESHOOTING_DOMAINS = (
    "development.md",
    "testing.md",
    "deployment.md",
    "dify.md",
    "data-cleaning.md",
)


def test_project_agents_is_small_and_does_not_store_dynamic_state():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.strip()
    assert len(agents.encode("utf-8")) < 8192
    for required in (
        "docs/README.md",
        "docs/功能与接口索引.md",
        "docs/项目目录结构.md",
        "docs/V2目标架构.md",
        "docs/V2决策记录.md",
        "maintain-project-troubleshooting",
        "次要歧义采用安全、可逆默认值",
        "每次修改必须在文档结尾追加记录",
    ):
        assert required in agents
    for dynamic_fact in ("当前镜像", "服务器 IP", "wheel SHA"):
        assert dynamic_fact not in agents


def test_document_index_routes_current_structure_and_troubleshooting():
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    for required in (
        "项目目录结构.md",
        "V2目标架构.md",
        "troubleshooting/README.md",
        "功能与接口索引.md",
        "功能阅读与更新矩阵",
        "只读对应权威文档",
    ):
        assert required in index
    for stable_path in (
        ROOT / "docs" / "项目目录结构.md",
        ROOT / "docs" / "功能与接口索引.md",
        ROOT / "docs" / "V2目标架构.md",
        ROOT / "docs" / "troubleshooting" / "README.md",
        ROOT / "docs" / "Codex项目上下文与文档维护规则.md",
    ):
        assert stable_path.is_file()


def test_as_built_structure_does_not_claim_target_packages_exist():
    structure = (ROOT / "docs" / "项目目录结构.md").read_text(encoding="utf-8")
    assert "当前实际结构" in structure
    assert "V2目标架构.md" in structure
    assert "不能把" in structure


def test_troubleshooting_index_has_all_runtime_domains():
    troubleshooting_root = ROOT / "docs" / "troubleshooting"
    index = (troubleshooting_root / "README.md").read_text(encoding="utf-8")
    for domain in TROUBLESHOOTING_DOMAINS:
        assert domain in index
        content = (troubleshooting_root / domain).read_text(encoding="utf-8")
        entries = content.split("\n## ")[1:]
        assert entries
        for entry in entries:
            for field in (
                "- 适用：",
                "- 触发：",
                "- 根因：",
                "- 解决：",
                "- 非解决：",
                "- 验证：",
                "- 预防：",
                "- 证据：",
                "- 最后验证：",
            ):
                assert field in entry


def test_project_troubleshooting_skill_is_discoverable_and_utf8():
    skill_root = ROOT / ".agents" / "skills" / "maintain-project-troubleshooting"
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    metadata = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert "name: maintain-project-troubleshooting" in skill
    for domain in TROUBLESHOOTING_DOMAINS:
        assert domain in skill
    assert "\ufffd" not in metadata
    assert "$maintain-project-troubleshooting" in metadata


def test_current_status_handoff_keeps_a_change_record():
    handoff = (ROOT / "AI管理服务当前状态与后续测试交接.md").read_text(
        encoding="utf-8"
    )
    assert "## 9. 文档修改记录" in handoff
    for field in ("修改原因", "修改前", "修改后"):
        assert field in handoff


def test_function_and_interface_index_is_current_and_routed():
    document = (ROOT / "docs" / "功能与接口索引.md").read_text(encoding="utf-8")
    assert not generate_project_index.changed_sections(ROOT, document)
    for section in generate_project_index.GENERATED_SECTIONS:
        assert f"<!-- GENERATED:{section}:START -->" in document
        assert f"<!-- GENERATED:{section}:END -->" in document

    readme = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "功能与接口索引.md" in readme


def test_static_route_index_matches_test_configured_fastapi_app():
    from ai_management.main import app

    indexed = {
        (route.method, route.path)
        for route in generate_project_index.collect_routes(ROOT)
    }
    actual = {
        (method, route.path)
        for route in app.routes
        if route.path not in generate_project_index.FRAMEWORK_ROUTE_PATHS
        for method in (route.methods or set())
    }
    assert indexed == actual


def test_index_check_does_not_require_application_import_or_service_secrets():
    environment = os.environ.copy()
    environment["APP_ENV"] = "production"
    for name in (
        "ADMIN_API_TOKEN",
        "INTERNAL_API_TOKEN",
        "USER_HMAC_SECRET",
        "DIFY_BUSINESS_APP_API_KEY",
        "DIFY_MOMENTS_APP_API_KEY",
        "DIFY_WRITING_APP_API_KEY",
        "DIFY_SUMMARY_PROFILE_APP_API_KEY",
    ):
        environment.pop(name, None)

    result = subprocess.run(
        [sys.executable, "scripts/generate_project_index.py", "--check"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0


def test_index_check_detects_stale_generated_content():
    document = (ROOT / "docs" / "功能与接口索引.md").read_text(encoding="utf-8")
    stale = document.replace(
        "<!-- GENERATED:HTTP:END -->",
        "| stale | GET | /stale | stale | stale |\n<!-- GENERATED:HTTP:END -->",
        1,
    )
    assert "HTTP" in generate_project_index.changed_sections(ROOT, stale)

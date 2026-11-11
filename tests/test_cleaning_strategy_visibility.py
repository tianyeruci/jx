from __future__ import annotations

from ai_management.api.routers import knowledge_admin


def test_admin_cleaning_strategy_choices_hide_all_course_route_strategies(monkeypatch):
    """Discovery only exposes general cleaning; explicit legacy APIs remain separate."""

    monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _kb_code: "dataset")

    general = {
        item["code"] for item in knowledge_admin.list_cleaning_strategies("general")["items"]
    }
    course = {item["code"] for item in knowledge_admin.list_cleaning_strategies("course")["items"]}
    visible = general | course

    required = {
        "personal_business_consultation",
        "wechat_public_account_cognition",
        "zsxq_post",
        "xiaobot_post",
        "lead_magnet",
    }
    assert required <= visible
    assert course == set()
    assert {"course_information", "learning_route", "course_route_catalog"}.isdisjoint(visible)

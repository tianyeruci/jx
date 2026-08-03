from __future__ import annotations

from ai_management.api.routers import knowledge_admin


def test_admin_cleaning_strategy_choices_expose_current_set_and_hide_legacy(monkeypatch):
    """The admin/test page uses the merged catalogue strategy only."""

    monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _kb_code: "dataset")

    general = {
        item["code"]
        for item in knowledge_admin.list_cleaning_strategies("general")["items"]
    }
    course = {
        item["code"]
        for item in knowledge_admin.list_cleaning_strategies("course")["items"]
    }
    visible = general | course

    required = {
        "personal_business_consultation",
        "wechat_public_account_cognition",
        "zsxq_post",
        "xiaobot_post",
        "lead_magnet",
        "course_route_catalog",
    }
    assert required <= visible
    assert "course_route_catalog" in course
    assert {"course_information", "learning_route"}.isdisjoint(visible)

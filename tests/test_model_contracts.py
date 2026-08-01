from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_management.model_cleaning import CourseEnrichmentResponse


def test_course_enrichment_normalizes_list_values_for_text_fields() -> None:
    result = CourseEnrichmentResponse.model_validate(
        {
            "direction_candidate": ["商业定位", "客户验证"],
            "description": ["帮助验证问题", "形成行动假设"],
            "problems": ["问题一"],
            "audience": ["创业者", "小团队负责人"],
            "highlights": ["客户访谈"],
            "learning_outcomes": ["形成定位假设"],
            "route_target": ["完成验证", "进入执行"],
        }
    )

    assert result.direction_candidate == "商业定位；客户验证"
    assert result.description == "帮助验证问题；形成行动假设"
    assert result.audience == "创业者；小团队负责人"
    assert result.route_target == "完成验证；进入执行"


def test_course_enrichment_still_rejects_links_after_normalization() -> None:
    with pytest.raises(ValidationError, match="不得返回链接"):
        CourseEnrichmentResponse.model_validate(
            {
                "direction_candidate": ["商业定位"],
                "description": ["https://example.com/should-not-be-returned"],
            }
        )

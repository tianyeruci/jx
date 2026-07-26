from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = ROOT / "test_data"


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} 缺少 YAML frontmatter"
    _, raw, _ = text.split("---", 2)
    metadata = yaml.safe_load(raw)
    assert isinstance(metadata, dict)
    return metadata


def test_synthetic_seed_v1_contract() -> None:
    expected = json.loads(
        (TEST_DATA / "合成测试种子v1.expected.json").read_text(encoding="utf-8")
    )

    course_path = TEST_DATA / expected["course_catalog"]["file"]
    course_metadata = _frontmatter(course_path)
    assert course_metadata == {
        "seed_id": expected["seed_id"],
        "test_only": True,
        "synthetic": True,
        "business_commitment": "none",
    }

    course_text = course_path.read_text(encoding="utf-8")
    course_records = [
        yaml.safe_load(block)
        for block in course_text.split("```yaml")[1:]
        for block in [block.split("```", 1)[0]]
    ]
    assert sum(record.get("record_type") == "course" for record in course_records) == 9
    assert sum(record.get("record_type") == "route" for record in course_records) == 2
    assert all("example.com" in record["official_url"] for record in course_records if record.get("record_type") == "course")

    samples = expected["knowledge_samples"]
    assert len(samples) == 6
    assert len({sample["source_type"] for sample in samples}) == 6
    for sample in samples:
        path = TEST_DATA / sample["file"]
        metadata = _frontmatter(path)
        assert metadata["seed_id"] == expected["seed_id"]
        assert metadata["source_type"] == sample["source_type"]
        assert metadata["test_only"] is True
        assert metadata["synthetic"] is True
        assert metadata["business_commitment"] == "none"
        assert "无真实业务承诺" in path.read_text(encoding="utf-8")


def test_business_advisor_seed_questions_and_no_fabrication_boundaries() -> None:
    expected = json.loads(
        (TEST_DATA / "合成测试种子v1.expected.json").read_text(encoding="utf-8")
    )
    dsl_path = ROOT / "dify" / "dsl" / "business_advisor.yml"
    dsl_text = dsl_path.read_text(encoding="utf-8")
    dsl = yaml.safe_load(dsl_text)

    questions = dsl["workflow"]["features"]["suggested_questions"]
    assert len(questions) == expected["suggested_questions_count"]
    assert len(set(questions)) == len(questions)
    assert all(isinstance(question, str) and question.strip() for question in questions)

    for boundary in expected["required_dsl_boundaries"]:
        assert boundary in dsl_text

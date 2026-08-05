from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from ai_management.main import app
from ai_management.models import SystemEventLog


def _headers() -> dict[str, str]:
    return {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }


def test_pdf_issue_center_filters_and_resolves(service_db) -> None:
    with service_db() as session:
        warning = SystemEventLog(
            level="WARNING",
            category="pdf_conversion",
            component="cleaning_worker",
            event_code="PDF_CONVERSION_WARNING",
            status="open",
            resource_type="cleaning_file",
            resource_id="file-safe-1",
            message="PDF 转换存在可复核问题",
            details={"knowledge_base_code": "general", "codes": ["table_review"]},
        )
        blocked = SystemEventLog(
            level="ERROR",
            category="pdf_conversion",
            component="cleaning_worker",
            event_code="PDF_CONVERSION_BLOCKED",
            status="open",
            resource_type="cleaning_file",
            resource_id="file-safe-2",
            message="PDF 转换失败",
            details={"knowledge_base_code": "course", "codes": ["ocr_failed"]},
            occurred_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add_all([warning, blocked])
        session.commit()
        warning_id = warning.id

    with TestClient(app) as client:
        response = client.get(
            "/internal/v1/admin/pdf-conversion-issues",
            params={"kb_code": "general", "status": "open", "severity": "warning"},
            headers=_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["details"]["knowledge_base_code"] == "general"

        resolved = client.post(
            f"/internal/v1/admin/pdf-conversion-issues/{warning_id}/resolve",
            json={"note": "人工复核完成"},
            headers=_headers(),
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"

        open_only = client.get(
            "/internal/v1/admin/pdf-conversion-issues",
            params={"status": "open"},
            headers=_headers(),
        )
        assert open_only.json()["total"] == 1

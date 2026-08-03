"""Safely backfill authoritative course collections from the reviewed XLSX.

This is an additive migration for catalogs imported before the collection
tables existed.  It never writes to Dify.  By default it validates the source
and the existing authority rows only; ``--apply`` is required for the atomic
database update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import delete, select

from ai_management.course_route_catalog import CourseRouteCatalogDraft, parse_course_route_catalog
from ai_management.courses import rebuild_snapshot
from ai_management.db import SessionLocal
from ai_management.models import Course, CourseCollection, CourseCollectionMember
from ai_management.services.admin_settings import bump_catalog_revision

DEFAULT_SOURCE = Path("文件清洗样本/课程信息与学习路线/课程信息与学习路线.xlsx")


def _normalize_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_existing_courses(session, draft: CourseRouteCatalogDraft) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for item in draft.courses:
        row = session.get(Course, item.course_id)
        if row is None:
            problems.append({"course_id": item.course_id, "reason": "missing_authority_course"})
            continue
        if _normalize_url(row.official_url) != _normalize_url(item.normalized_url):
            problems.append({"course_id": item.course_id, "reason": "official_url_mismatch"})
    return problems


def _apply(session, draft: CourseRouteCatalogDraft, *, source_file: str) -> dict[str, int]:
    target_collection_ids = {item.collection_id for item in draft.collections}
    target_course_ids = {item.course_id for item in draft.courses}
    for item in draft.courses:
        row = session.get(Course, item.course_id)
        if row is None:  # guarded by _validate_existing_courses
            raise RuntimeError(f"课程权威行不存在：{item.course_id}")
        row.series_name = item.series_name or None

    for item in draft.collections:
        row = session.get(CourseCollection, item.collection_id)
        if row is None:
            row = CourseCollection(collection_id=item.collection_id)
            session.add(row)
        row.name = item.name
        row.raw_title = item.raw_title
        row.declared_lessons = item.declared_lessons
        row.total_minutes = item.total_minutes
        row.entry_course_id = item.entry_course_id or None
        row.enabled = True
        row.review_status = "approved"
        row.source_file = source_file
        row.warnings = list(item.warnings)
        session.flush()
        session.execute(
            delete(CourseCollectionMember).where(
                CourseCollectionMember.collection_id == item.collection_id,
                ~CourseCollectionMember.course_id.in_(item.member_course_ids),
            )
        )
        for order_rank, course_id in zip(item.member_orders, item.member_course_ids, strict=True):
            member = session.scalar(
                select(CourseCollectionMember).where(
                    CourseCollectionMember.collection_id == item.collection_id,
                    CourseCollectionMember.course_id == course_id,
                )
            )
            if member is None:
                session.add(
                    CourseCollectionMember(
                        collection_id=item.collection_id,
                        course_id=course_id,
                        order_rank=order_rank,
                        is_entry=course_id == item.entry_course_id,
                    )
                )
            else:
                member.order_rank = order_rank
                member.is_entry = course_id == item.entry_course_id

    # Do not touch collections outside the reviewed source.  This makes the
    # one-off backfill reversible and safe if another catalog is present.
    session.flush()
    bump_catalog_revision(session)
    session.flush()
    snapshot = rebuild_snapshot(session)
    return {
        "courses_updated": len(target_course_ids),
        "collections_updated": len(target_collection_ids),
        "members_expected": sum(len(item.member_course_ids) for item in draft.collections),
        "snapshot_courses": len(snapshot.get("courses", {})),
        "snapshot_routes": len(snapshot.get("routes", {})),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    digest = _source_sha256(source)
    if args.expected_source_sha256 and digest.lower() != args.expected_source_sha256.lower():
        raise SystemExit("source SHA256 does not match --expected-source-sha256")
    draft = parse_course_route_catalog(source, existing_course_ids_by_url={})
    with SessionLocal() as session:
        problems = _validate_existing_courses(session, draft)
        result = {
            "source": source.name,
            "source_sha256": digest,
            "courses_in_source": len(draft.courses),
            "collections_in_source": len(draft.collections),
            "routes_in_source": len(draft.routes),
            "existing_course_problems": problems,
            "applied": bool(args.apply),
        }
        if problems:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2
        if args.apply:
            result.update(_apply(session, draft, source_file=source.name))
            session.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

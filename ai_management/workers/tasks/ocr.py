"""Dedicated OCR queue entry point for PDF cleaning jobs.

The existing cleaning implementation owns the transactional source-file
state machine.  This task deliberately delegates to it instead of copying
that logic; only the queue, timeout and worker isolation differ for PDFs.
"""

from __future__ import annotations

from pathlib import Path

from ...config import get_settings
from ..celery_app import celery_app

settings = get_settings()


def cleaning_queue_for_filename(filename: str) -> str:
    """Return the queue that should process a source file.

    Every PDF enters ``ai_ocr`` so the worker can apply the configured OCR
    switch and page/quality gates.  Other formats remain on ``ai_cleaning``.
    """

    return "ai_ocr" if Path(filename).suffix.lower() == ".pdf" else "ai_cleaning"


@celery_app.task(
    name="ai_management.process_ocr_cleaning_file",
    bind=True,
    autoretry_for=(),
    acks_late=True,
    soft_time_limit=max(540, settings.pdf_ocr_task_timeout_seconds - 60),
    time_limit=settings.pdf_ocr_task_timeout_seconds,
)
def process_ocr_cleaning_file(self, file_id: str) -> bool:
    """Run the shared cleaning transaction inside the isolated OCR worker."""

    from .cleaning import process_cleaning_file

    # ``Task.run`` is already bound to the shared cleaning task.  Passing the
    # OCR task explicitly would bind a third positional argument and leave
    # every PDF stuck in ``processing`` with a TypeError.  Calling the bound
    # method keeps the shared transaction/retry semantics without duplicating
    # the source publication state machine.
    return process_cleaning_file.run(file_id)


__all__ = ["cleaning_queue_for_filename", "process_ocr_cleaning_file"]

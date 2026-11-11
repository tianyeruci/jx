"""Offline, lazy RapidOCR backend used for scanned PDF pages.

The backend deliberately keeps the optional OCR imports and model session out
of module import time.  A worker can therefore start with OCR disabled and
only pay the model startup cost when a scan-only PDF is actually submitted.
"""

from __future__ import annotations

import importlib
import inspect
import threading
from pathlib import Path
from typing import Any, Mapping

from . import pdf_markdown_refine as _layout_core
from .pdf_convert import (
    OptionalBackendUnavailable,
    PDFPageResult,
    _compact_length,
    _normalize_markdown,
    validate_offline_ocr_models,
)


class RapidOCRBackend:
    """RapidOCR ONNX Runtime backend with one reusable process-local session."""

    name = "rapidocr_onnxruntime"
    _lock = threading.RLock()
    _engines: dict[tuple[str, str, int], Any] = {}

    def __init__(
        self,
        *,
        device: str | None = "cpu",
        render_dpi: int = 200,
        retry_dpi: int = 300,
        model_root: str | Path | None = None,
        model_manifest: Mapping[str, str] | None = None,
        cpu_threads: int = 2,
    ) -> None:
        self.device = device or "cpu"
        self.render_dpi = render_dpi
        self.retry_dpi = retry_dpi
        self.model_root = Path(model_root) if model_root is not None else None
        self.model_manifest = dict(model_manifest or {})
        self.cpu_threads = max(1, int(cpu_threads))

    def _engine(self) -> Any:
        if self.model_root is not None or self.model_manifest:
            if self.model_root is None:
                raise OptionalBackendUnavailable("OCR离线模型目录未配置")
            validate_offline_ocr_models(self.model_root, self.model_manifest)
        try:
            rapidocr = importlib.import_module("rapidocr")
            engine_class = getattr(rapidocr, "RapidOCR")
        except (ImportError, AttributeError) as exc:
            raise OptionalBackendUnavailable(
                "RapidOCR 未安装；扫描PDF已转入review，未生成伪正文"
            ) from exc

        root_key = str(self.model_root.resolve()) if self.model_root else ""
        key = (root_key, self.device, self.cpu_threads)
        with self._lock:
            cached = self._engines.get(key)
            if cached is not None:
                return cached
            kwargs: dict[str, Any] = {}
            # RapidOCR releases have used both a model_path keyword and a
            # parameter object.  Pass only options supported by the installed
            # release; falling back to the default bundled CPU models remains
            # safe because the container has network access disabled.
            try:
                parameters = inspect.signature(engine_class).parameters
            except (TypeError, ValueError):
                parameters = {}
            if self.model_root is not None:
                for name in ("model_path", "model_root_dir", "model_dir"):
                    if name in parameters:
                        kwargs[name] = str(self.model_root)
                        break
            for name in ("intra_op_num_threads", "cpu_threads", "num_threads"):
                if name in parameters:
                    kwargs[name] = self.cpu_threads
                    break
            try:
                engine = engine_class(**kwargs)
            except TypeError:
                engine = engine_class()
            self._engines[key] = engine
            return engine

    @staticmethod
    def _result_fields(result: Any) -> tuple[list[str], list[float], list[Any]]:
        """Read RapidOCR 3.x objects and older tuple-shaped results."""

        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        boxes = getattr(result, "boxes", None)
        if texts is None and isinstance(result, (tuple, list)):
            # Older rapidocr-onnxruntime returned (boxes, txts, scores).
            if len(result) >= 3:
                boxes, texts, scores = result[:3]
            elif len(result) == 2:
                texts, scores = result
        texts = [] if texts is None else list(texts)
        scores = [] if scores is None else [float(value) for value in scores]
        boxes = [] if boxes is None else list(boxes)
        # Keep the three OCR fields aligned.  Filtering empty text after
        # independently slicing score/box arrays used to attach the blank
        # row's confidence and geometry to the next visible line.
        aligned: list[tuple[str, float, Any]] = []
        for index, raw_text in enumerate(texts):
            cleaned = _layout_core._clean_text(raw_text)
            if not cleaned:
                continue
            score = scores[index] if index < len(scores) else 0.0
            box = boxes[index] if index < len(boxes) else None
            aligned.append((cleaned, score, box))
        return (
            [text for text, _, _ in aligned],
            [score for _, score, _ in aligned],
            [box for _, _, box in aligned],
        )

    @staticmethod
    def _to_markdown(result: Any) -> tuple[str, float | None]:
        texts, scores, boxes = RapidOCRBackend._result_fields(result)
        rows: list[tuple[float, float, str, float]] = []
        for index, text in enumerate(texts):
            top = float(index)
            left = float(index)
            if index < len(boxes):
                try:
                    points = boxes[index]
                    left = min(float(point[0]) for point in points)
                    top = min(float(point[1]) for point in points)
                except (TypeError, ValueError, IndexError):
                    pass
            rows.append((top, left, text, scores[index] if index < len(scores) else 0.0))
        rows.sort(key=lambda item: (item[0], item[1]))
        lines: list[str] = []
        line_tops: list[float] = []
        line_scores: list[list[float]] = []
        for top, _left, text, score in rows:
            if not line_tops or abs(top - line_tops[-1]) > 8:
                line_tops.append(top)
                lines.append(text)
                line_scores.append([score])
            else:
                lines[-1] = f"{lines[-1]} {text}".strip()
                line_scores[-1].append(score)
        markdown = _normalize_markdown("\n".join(lines))
        flat_scores = [score for scores_for_line in line_scores for score in scores_for_line]
        confidence = sum(flat_scores) / len(flat_scores) if flat_scores else None
        return markdown, confidence

    def extract(
        self,
        path: Path,
        *,
        dpi: int = 200,
        page_numbers: set[int] | None = None,
    ) -> list[PDFPageResult]:
        try:
            pdfium = importlib.import_module("pypdfium2")
            import numpy as np
        except ImportError as exc:
            raise OptionalBackendUnavailable(
                "RapidOCR扫描PDF需要pypdfium2和numpy；未生成伪正文"
            ) from exc
        engine = self._engine()
        try:
            document = pdfium.PdfDocument(str(path))
        except Exception as exc:
            raise OptionalBackendUnavailable(f"PDF栅格化失败:{type(exc).__name__}") from exc
        pages: list[PDFPageResult] = []
        try:
            for index in range(len(document)):
                if page_numbers is not None and index + 1 not in page_numbers:
                    continue
                page = document[index]
                bitmap = page.render(scale=float(dpi) / 72.0)
                image = np.asarray(bitmap.to_pil())
                result = engine(image)
                markdown, confidence = self._to_markdown(result)
                warnings: list[str] = []
                if not markdown:
                    warnings.append("ocr_page_empty")
                pages.append(
                    PDFPageResult(
                        page_number=index + 1,
                        markdown=markdown,
                        backend=self.name,
                        text_characters=_compact_length(markdown),
                        warnings=warnings,
                        confidence=confidence,
                        dpi=dpi,
                        ocr_attempts=1,
                    )
                )
                close_page = getattr(page, "close", None)
                if callable(close_page):
                    close_page()
        finally:
            close_document = getattr(document, "close", None)
            if callable(close_document):
                close_document()
        return pages


__all__ = ["RapidOCRBackend"]

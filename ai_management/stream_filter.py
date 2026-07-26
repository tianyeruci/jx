from __future__ import annotations

import re

URL_START = re.compile(r"https?://|www\.", re.IGNORECASE)
URL_END = re.compile(r"\s")


class StreamingUrlRedactor:
    """Remove model-generated URLs without waiting for the full streamed answer."""

    def __init__(self, replacement: str = "（链接请使用课程卡片或引用）") -> None:
        self.replacement = replacement
        self.pending = ""
        self.suppressing = False
        self.replacement_emitted = False

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self.pending += text
        output: list[str] = []
        while self.pending:
            if self.suppressing:
                match = URL_END.search(self.pending)
                if match is None:
                    self.pending = ""
                    break
                if not self.replacement_emitted:
                    output.append(self.replacement)
                output.append(self.pending[match.start() : match.end()])
                self.pending = self.pending[match.end() :]
                self.suppressing = False
                self.replacement_emitted = False
                continue

            match = URL_START.search(self.pending)
            if match is not None:
                output.append(self.pending[: match.start()])
                self.pending = self.pending[match.end() :]
                self.suppressing = True
                continue

            # Keep enough tail to detect a URL prefix split across SSE chunks.
            keep = min(8, len(self.pending))
            if len(self.pending) <= keep:
                break
            output.append(self.pending[:-keep])
            self.pending = self.pending[-keep:]
        return "".join(output)

    def finish(self) -> str:
        if self.suppressing:
            result = "" if self.replacement_emitted else self.replacement
        else:
            result = self.pending
        self.pending = ""
        self.suppressing = False
        self.replacement_emitted = False
        return result


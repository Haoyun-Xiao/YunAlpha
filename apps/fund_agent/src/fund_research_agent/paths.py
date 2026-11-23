from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from fund_research_agent.domain.errors import WriteBoundaryViolation


class WriteBoundary:
    """Enforce that every application write remains inside the new project's var directory."""

    def __init__(
        self, project_root: Path, legacy_root: Path, write_root: Path | None = None
    ) -> None:
        self.project_root = project_root.resolve()
        self.legacy_root = legacy_root.resolve()
        self.write_root = (write_root or (self.project_root / "var")).resolve()

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def assert_writable(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve()
        if self._is_within(resolved, self.legacy_root):
            raise WriteBoundaryViolation(f"禁止写入旧项目：{resolved}")
        if not self._is_within(resolved, self.write_root):
            raise WriteBoundaryViolation(f"写入必须位于 {self.write_root}：{resolved}")
        return resolved

    def ensure_dir(self, path: str | Path) -> Path:
        target = self.assert_writable(path)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def write_text(self, path: str | Path, content: str) -> Path:
        return self.write_bytes(path, content.encode("utf-8"))

    def write_bytes(self, path: str | Path, content: bytes) -> Path:
        target = self.assert_writable(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".atomic-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            self.assert_writable(target)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

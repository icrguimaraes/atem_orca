import hashlib
from pathlib import Path

from app.config import get_settings


class LocalStorage:
    """Armazenamento em disco (volume Railway). Interface mínima para trocar por S3 depois."""

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or get_settings().upload_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, content: bytes, suffix: str) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / f"{digest}{suffix}"
        if not path.exists():
            path.write_bytes(content)
        return str(path), digest

    def read(self, path: str) -> bytes:
        return Path(path).read_bytes()


def get_storage() -> LocalStorage:
    return LocalStorage()

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    root: Path
    doc_dir: Path
    data_dir: Path
    api_url: str
    api_key: str
    model: str
    reasoning_effort: str
    timeout: float | None
    ipv4_first: bool
    soffice: str

    @classmethod
    def load(cls, root: Path | None = None):
        root = (root or Path.cwd()).resolve()
        load_dotenv(root / ".env", override=False)
        def path(key, default):
            value = Path(os.getenv(key, default)).expanduser()
            return (root / value).resolve() if not value.is_absolute() else value.resolve()
        timeout_ms = int(os.getenv("GLM_API_TIMEOUT_MS", "180000"))
        return cls(
            root=root, doc_dir=path("SEMANTIC_DOC_DIR", "rawdoc"),
            data_dir=path("SEMANTIC_DATA_DIR", ".semantic"),
            api_url=os.getenv("GLM_API_URL", "").rstrip("/"),
            api_key=os.getenv("GLM_API_KEY", os.getenv("ZHIPU_API_KEY", "")),
            model=os.getenv("GLM_MODEL", "glm-5.3"),
            reasoning_effort=os.getenv("GLM_REASONING_EFFORT", "high"),
            timeout=None if timeout_ms == 0 else timeout_ms / 1000,
            ipv4_first=os.getenv("GLM_DNS_RESULT_ORDER", "") == "ipv4first",
            soffice=os.getenv("SEMANTIC_SOFFICE_PATH", ""),
        )

    @property
    def endpoint(self):
        if self.api_url.endswith("/chat/completions"):
            return self.api_url
        # A host-only OpenAI-compatible proxy normally serves /v1.
        base = self.api_url + ("/v1" if not urlsplit(self.api_url).path.strip("/") else "")
        return base + "/chat/completions"

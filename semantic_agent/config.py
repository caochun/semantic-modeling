from dataclasses import dataclass, replace
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
    max_output_tokens: int = 0
    deepseek_api_url: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    thinking_enabled: bool | None = None

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
            deepseek_api_url=os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            timeout=None if timeout_ms == 0 else timeout_ms / 1000,
            ipv4_first=os.getenv("GLM_DNS_RESULT_ORDER", "") == "ipv4first",
            soffice=os.getenv("SEMANTIC_SOFFICE_PATH", ""),
            max_output_tokens=max(0, int(os.getenv("GLM_MAX_OUTPUT_TOKENS", "0"))),
        )

    @property
    def endpoint(self):
        if self.api_url.endswith("/chat/completions"):
            return self.api_url
        # A host-only OpenAI-compatible proxy normally serves /v1.
        base = self.api_url + ("/v1" if not urlsplit(self.api_url).path.strip("/") else "")
        return base + "/chat/completions"

    def for_model(self, model, reasoning_effort=None):
        """Return request settings for a whitelisted provider/model profile."""
        model = model or self.model
        effort = reasoning_effort or self.reasoning_effort
        if model.startswith('deepseek-'):
            if model not in {'deepseek-flash', 'deepseek-v4-pro'}:
                raise ValueError('不支持的 DeepSeek 模型')
            if effort not in {'on', 'off', 'high'}:
                raise ValueError('DeepSeek 思考模式必须选择 on 或 off')
            deepseek_url = self.deepseek_api_url
            if not deepseek_url.endswith('/chat/completions'):
                deepseek_url += '/chat/completions'
            enabled = effort != 'off'
            return replace(self, api_url=deepseek_url, api_key=self.deepseek_api_key,
                           model=model, reasoning_effort='high' if enabled else '',
                           thinking_enabled=enabled)
        if model != 'glm-5.3':
            raise ValueError('不支持的模型')
        if effort not in {'low', 'high', 'max'}:
            raise ValueError('GLM-5.3 推理强度必须是 low、high 或 max')
        return replace(self, model=model, reasoning_effort=effort)

    def model_profiles(self):
        return [
            {'id': 'glm-5.3', 'provider': '智谱 Coding Plan', 'configured': bool(self.api_key and self.api_url),
             'efforts': ['low', 'high', 'max'], 'default_effort': self.reasoning_effort if self.model == 'glm-5.3' else 'high'},
            {'id': 'deepseek-flash', 'provider': 'DeepSeek', 'configured': bool(self.deepseek_api_key),
             'efforts': ['on', 'off'], 'default_effort': 'on'},
            {'id': 'deepseek-v4-pro', 'provider': 'DeepSeek', 'configured': bool(self.deepseek_api_key),
             'efforts': ['on', 'off'], 'default_effort': 'on'},
        ]

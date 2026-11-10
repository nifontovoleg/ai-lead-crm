from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    mode: str = "demo"
    api_key: str = ""
    base_url: str = "https://api.proxyapi.ru/v1"
    model: str = "openai/gpt-4o-mini"
    credentials: Path = ROOT / "secrets/google-service-account.json"
    spreadsheet_id: str = ""
    telegram_token: str = ""
    chat_id: str = ""
    trigger_token: str = ""
    threshold: int = 70
    batch_size: int = 5
    timeout: int = 45
    live_enabled: bool = False
    data_dir: Path = ROOT / "data"

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env", override=False)
        credential = Path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/google-service-account.json"))
        cfg = cls(
            mode=os.getenv("CRM_MODE", "demo"),
            api_key=os.getenv("PROXYAPI_KEY", ""),
            base_url=os.getenv("PROXYAPI_BASE_URL", "https://api.proxyapi.ru/v1"),
            model=os.getenv("PROXYAPI_MODEL", "openai/gpt-4o-mini"),
            credentials=credential if credential.is_absolute() else ROOT / credential,
            spreadsheet_id=os.getenv("GOOGLE_SPREADSHEET_ID", ""),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            trigger_token=os.getenv("CRM_TRIGGER_TOKEN", ""),
            threshold=int(os.getenv("HOT_LEAD_THRESHOLD", "70")),
            batch_size=int(os.getenv("MAX_LEADS_PER_RUN", "5")),
            timeout=int(os.getenv("AI_TIMEOUT_SECONDS", "45")),
            live_enabled=os.getenv("LIVE_PROCESSING_ENABLED", "false").lower() == "true",
        )
        if cfg.mode not in {"demo", "live"}:
            raise ValueError("CRM_MODE должен быть demo или live")
        if not 1 <= cfg.threshold <= 100 or not 1 <= cfg.batch_size <= 20 or not 5 <= cfg.timeout <= 90:
            raise ValueError("Неверные threshold, batch_size или timeout")
        return cfg

    def require_live(self, processing=False):
        missing = []
        if not self.spreadsheet_id:
            missing.append("GOOGLE_SPREADSHEET_ID")
        if not self.credentials.is_file():
            missing.append("GOOGLE_SERVICE_ACCOUNT_FILE")
        if processing:
            for name, value in [("PROXYAPI_KEY", self.api_key), ("TELEGRAM_BOT_TOKEN", self.telegram_token), ("TELEGRAM_CHAT_ID", self.chat_id)]:
                if not value:
                    missing.append(name)
            if not self.live_enabled:
                missing.append("LIVE_PROCESSING_ENABLED=true")
        if missing:
            raise ValueError("Требуется настройка: " + ", ".join(missing))
        if self.base_url.rstrip("/") != "https://api.proxyapi.ru/v1":
            raise ValueError("Для этого проекта разрешён только официальный адрес ProxyAPI")

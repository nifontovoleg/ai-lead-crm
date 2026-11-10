import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

INPUT_COLUMNS = ["ID", "Дата", "Имя", "Источник", "Бюджет", "Контакты за 7 дней", "Дней без контакта", "Ответил", "Статус"]
OUTPUT_COLUMNS = ["AI балл", "Приоритет", "Объяснение", "Рекомендация", "Обработано UTC", "Режим оценки", "Fingerprint", "Уведомление", "Ошибка"]
COLUMNS = INPUT_COLUMNS + OUTPUT_COLUMNS
ACTIVE_STATUSES = {"Новый", "В работе", "Переговоры"}
STATUSES = ACTIVE_STATUSES | {"Выигран", "Проигран"}
SOURCES = {"Рекомендация": 25, "Сайт": 20, "Telegram": 15, "Реклама": 10, "Другое": 5}
RULE_VERSION = "v1"


class Lead(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    source: str
    budget: int = Field(ge=0, le=100_000_000)
    contacts: int = Field(ge=0, le=1000)
    days: int = Field(ge=0, le=3650)
    replied: bool
    status: str

    @field_validator("source")
    @classmethod
    def source_known(cls, value):
        if value not in SOURCES:
            raise ValueError("Неизвестный источник")
        return value

    @field_validator("status")
    @classmethod
    def status_known(cls, value):
        if value not in STATUSES:
            raise ValueError("Неизвестный статус")
        return value

    @classmethod
    def from_row(cls, row):
        answer = str(row.get("Ответил", "")).strip().lower()
        if answer not in {"да", "нет"}:
            raise ValueError("Ответил: укажите Да или Нет")
        return cls(id=str(row.get("ID", "")).strip(), name=str(row.get("Имя", "")).strip(),
                   source=str(row.get("Источник", "")).strip(), budget=row.get("Бюджет"),
                   contacts=row.get("Контакты за 7 дней"), days=row.get("Дней без контакта"),
                   replied=answer == "да", status=str(row.get("Статус", "")).strip())

    def features(self):
        # Имя и ID не нужны модели и не отправляются провайдеру.
        return self.model_dump(include={"source", "budget", "contacts", "days", "replied"})

    def fingerprint(self, scorer_version):
        payload = {"features": self.features(), "rules": RULE_VERSION, "scorer": scorer_version}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Score(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    score: int = Field(ge=0, le=100)
    explanation: str = Field(min_length=10, max_length=700)
    recommendation: str = Field(min_length=10, max_length=700)


class RunResult(BaseModel):
    mode: Literal["demo", "live"]
    processed: int = 0
    skipped: int = 0
    invalid: int = 0
    errors: int = 0
    notifications: int = 0
    deferred: int = 0


def priority(score, threshold=70):
    if score >= threshold:
        return "Горячий"
    return "Тёплый" if score >= min(40, threshold) else "Холодный"

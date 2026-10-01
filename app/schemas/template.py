import re
import uuid
from typing import Self

from pydantic import BaseModel, Field, model_validator

PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")
MAX_PLACEHOLDERS = 50
_BRACES = r"\{\{.*?\}\}"


def extract_placeholders(*texts: str | None) -> list[str]:
    found: list[str] = []
    for text in texts:
        for token in re.findall(_BRACES, text or "", flags=re.S):
            if not PLACEHOLDER_PATTERN.fullmatch(token):
                raise ValueError(f"올바르지 않은 플레이스홀더 형식입니다: {token}")
            if token not in found:
                found.append(token)
    return found


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def check_placeholders(self) -> Self:
        if len(extract_placeholders(self.title, self.body)) > MAX_PLACEHOLDERS:
            raise ValueError(f"플레이스홀더는 최대 {MAX_PLACEHOLDERS}개까지 사용할 수 있습니다.")
        return self


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    title: str | None
    body: str
    placeholders: list[str]

import uuid
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["ios", "android", "sms", "email"]


class DeviceIn(BaseModel):
    channel: Channel
    token: str = Field(min_length=1, max_length=512)


class DeviceOut(BaseModel):
    id: uuid.UUID
    channel: Channel
    token: str

from pydantic import BaseModel, Field

from app.schemas.device import Channel


class PreferenceUpdate(BaseModel):
    preferences: dict[Channel, bool] = Field(min_length=1)

import asyncio
import random
from collections import deque
from datetime import UTC, datetime


class ThirdPartyMock:
    MAX_RECORDS = 10_000

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self.records: deque[dict] = deque(maxlen=self.MAX_RECORDS)
        self._success_rate = 100
        self._delay_ms = 0

    @property
    def success_rate(self) -> int:
        return self._success_rate

    @success_rate.setter
    def success_rate(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ValueError("success_rate는 0 이상 100 이하의 정수여야 합니다.")
        self._success_rate = value

    @property
    def delay_ms(self) -> int:
        return self._delay_ms

    @delay_ms.setter
    def delay_ms(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 30_000:
            raise ValueError("delay_ms는 0 이상 30000 이하의 정수여야 합니다.")
        self._delay_ms = value

    async def send(self, notification: dict) -> bool:
        if self._delay_ms:
            await asyncio.sleep(self._delay_ms / 1000)
        success = self._rng.randint(1, 100) <= self._success_rate
        self.records.append(
            {
                "received_at": datetime.now(UTC).isoformat(),
                "channel": notification["channel"],
                "recipient_id": notification["recipient_id"],
                "body": notification["body"],
            }
        )
        return success

    def get_records(self) -> list[dict]:
        return list(self.records)

    def reset(self) -> None:
        self.records.clear()
        self._success_rate = 100
        self._delay_ms = 0


class APNSMock(ThirdPartyMock):
    pass


class FCMMock(ThirdPartyMock):
    pass


class TwilioMock(ThirdPartyMock):
    pass


class SendGridMock(ThirdPartyMock):
    pass


MOCKS: dict[str, ThirdPartyMock] = {
    "ios": APNSMock(),
    "android": FCMMock(),
    "sms": TwilioMock(),
    "email": SendGridMock(),
}

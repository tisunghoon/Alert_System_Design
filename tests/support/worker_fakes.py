import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.models import Notification

EVENT_ID = "evt-1"
NOW = 1_000_000.0


class FakeDB:
    def __init__(self, notification, history, events):
        self.notification = notification
        self.history = history
        self.events = events
        self.commits = 0

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self.notification
        return result

    async def get(self, _model, _id):
        return self.notification

    def add(self, obj):
        self.history.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1
        self.events.append("commit")


class FakeSessions:
    def __init__(self, notification):
        self.notification = notification
        self.history = []
        self.events = []
        self.fail = False

    def __call__(self):
        @asynccontextmanager
        async def session():
            if self.fail:
                raise ConnectionError("db down")
            yield FakeDB(self.notification, self.history, self.events)

        return session()


def make_notification(status="QUEUED"):
    return Notification(
        id=uuid.uuid4(),
        event_id=EVENT_ID,
        app_id=uuid.uuid4(),
        channel="ios",
        recipient_id="u1",
        body="hi",
        status=status,
        retry_count=0,
        queued_at=datetime.now(UTC),
    )


def message(retry_count=0, **extra):
    return {
        "event_id": EVENT_ID,
        "channel": "ios",
        "recipient_id": "u1",
        "title": "t",
        "body": "hi",
        "retry_count": retry_count,
        **extra,
    }

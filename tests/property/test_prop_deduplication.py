import fakeredis
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services import deduplication
from tests.support.db_fakes import make_db


@settings(max_examples=50, deadline=None)
@given(event_id=st.text(min_size=1, max_size=256))
async def test_second_request_with_same_event_id_is_duplicate(event_id):
    """Feature: alert-system, Property 3: 같은 Event_ID를 처리한 뒤에는 이후 요청이 항상 중복으로 판정된다"""
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    db = make_db(False)

    assert not await deduplication.check_duplicate(event_id, db, client=client)
    await deduplication.mark_processed(event_id, client=client)
    assert await deduplication.check_duplicate(event_id, db, client=client)


# Cache가 비어 있어도 DB에 성공 기록이 있으면 항상 중복이다
@settings(max_examples=50, deadline=None)
@given(event_id=st.text(min_size=1, max_size=256))
async def test_delivered_record_is_duplicate_without_cache(event_id):
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    assert await deduplication.check_duplicate(event_id, make_db(True), client=client)

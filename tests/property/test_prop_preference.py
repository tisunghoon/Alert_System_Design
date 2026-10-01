from hypothesis import given
from hypothesis import strategies as st

from app.mocks.third_party_mock import ThirdPartyMock
from app.models.base import CHANNELS
from app.services.preference_service import is_channel_enabled


@given(
    preferences=st.dictionaries(st.sampled_from(CHANNELS), st.booleans()),
    channel=st.sampled_from(CHANNELS),
)
async def test_prop_disabled_channel_never_reaches_mock(preferences, channel):
    """Feature: alert-system, Property 14: User_Preference 비활성화 채널 스킵"""
    mock = ThirdPartyMock()

    if is_channel_enabled(preferences, channel):
        await mock.send({"channel": channel, "recipient_id": "u1", "body": "hi"})

    disabled = preferences.get(channel) is False
    assert (len(mock.records) == 0) == disabled

from app.zalo import parse_update

PAYLOAD = {"ok": True,"result": {"event_name": "message.text.received", "message": {
    "from": {"id": "person"}, "chat": {"id": "chat-1", "chat_type": "PRIVATE"},
    "text": "bảo hành sao?", "message_id": "mid-101", "date": 1750316131602}}}


def test_official_webhook_payload():
    assert parse_update(PAYLOAD) == {"chat_id": "chat-1", "event_id": "mid-101", "text": "bảo hành sao?"}


def test_ignore_group_or_non_text():
    from copy import deepcopy
    group = deepcopy(PAYLOAD)
    group["result"]["message"]["chat"]["chat_type"] = "GROUP"
    assert parse_update(group) is None
    image = deepcopy(PAYLOAD)
    image["result"]["event_name"] = "message.image.received"
    assert parse_update(image) is None

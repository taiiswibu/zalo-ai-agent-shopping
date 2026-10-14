import httpx
import logging
import pytest
from app.zalo import ZaloClient


def client_with_result(monkeypatch, payload, status=200):
    original = httpx.Client
    def handler(request):
        return httpx.Response(status, json=payload)
    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr('app.zalo.httpx.Client', factory)
    return ZaloClient(token='FAKE_BOT_TOKEN_NEVER_USE', mock=False)


def test_408_empty_poll_is_not_error(monkeypatch):
    client=client_with_result(monkeypatch, {'ok': False, 'error_code': 408, 'description': 'Request timeout'})
    assert client.call('getUpdates', {'timeout':'25'})=={'ok':True,'result':[]}


def test_other_error_contains_code_but_not_bot_secret(monkeypatch):
    token='FAKE_BOT_TOKEN_NEVER_USE'
    client=client_with_result(monkeypatch, {'ok': False, 'error_code': 409,
        'description': f'Conflict path /bot{token}/getUpdates'}, 200)
    with pytest.raises(RuntimeError,match='error_code=409') as exc:
        client.call('getUpdates')
    assert token not in str(exc.value)
    assert '[REDACTED]' in str(exc.value)


def test_sender_success(monkeypatch):
    client=client_with_result(monkeypatch, {'ok': True, 'result': {'message_id':'test'}})
    assert client.send_message('chat','hello')['ok'] is True


def test_error_http_status_does_not_leak_token(monkeypatch):
    client=client_with_result(monkeypatch, {'ok':False, 'error_code':401,
        'description': 'Unauthorized'}, 401)
    with pytest.raises(RuntimeError,match='HTTP=401') as exc:
        client.call('getMe')
    assert client.token not in str(exc.value)


def test_httpx_logger_not_info():
    assert logging.getLogger('httpx').getEffectiveLevel() >= logging.WARNING

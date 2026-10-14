from __future__ import annotations
import hmac
import logging
import re
import httpx
from app.config import settings

BASE = "https://bot-api.zaloplatforms.com"

# httpx normally prints the entire URL for every outgoing request.
# Zalo embeds BOT_TOKEN in URL path, so disable its INFO/DEBUG logging.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _safe_description(value: object, token: str) -> str:
    """Sanitize provider-supplied error text before it can hit logs."""
    text = str(value or "no description")
    if token:
        text = text.replace(token, "[REDACTED]")
    text = re.sub(r"/bot[^/\s]+/", "/bot[REDACTED]/", text)
    return text[:180]


def verify_secret(value: str | None) -> bool:
    return bool(settings.webhook_secret and len(settings.webhook_secret) >= 8 and not settings.webhook_secret.startswith("replace-")
                and value and hmac.compare_digest(settings.webhook_secret, value))


def parse_update(payload: dict) -> dict | None:
    """Zalo Bot official schema: {ok:true,result:{event_name,message:{chat:{id},text,message_id}}}."""
    if not isinstance(payload, dict):
        return None
    result = payload.get("result", {})
    if not isinstance(result, dict) or result.get("event_name") != "message.text.received":
        return None
    msg = result.get("message", {})
    if not isinstance(msg, dict):
        return None
    chat_id = str(msg.get("chat", {}).get("id", "")) if isinstance(msg.get("chat"), dict) else ""
    message_id = str(msg.get("message_id", ""))
    if not chat_id or not message_id or not isinstance(msg.get("text"), str):
        return None
    # GROUP is beta; respond only to private chats to prevent unsolicited messages.
    if msg["chat"].get("chat_type", "PRIVATE") != "PRIVATE":
        return None
    return {"chat_id": chat_id, "event_id": message_id, "text": msg["text"][:1000]}


class ZaloClient:
    def __init__(self, token: str | None = None, mock: bool | None = None):
        self.token = settings.bot_token if token is None else token
        self.mock = (settings.bot_mode == "mock") if mock is None else mock

    def call(self, method: str, payload: dict | None = None, *, timeout: float = 15) -> dict:
        if not self.token:
            raise RuntimeError("BOT_TOKEN chưa được cấu hình")
        allowed = {"sendMessage", "setWebhook", "deleteWebhook", "getWebhookInfo",
                   "getUpdates", "getMe", "testWebhook"}
        if method not in allowed:
            raise ValueError("Unknown Bot API method")

        # Never use response.raise_for_status(): HTTPX exceptions can include
        # the raw request URL, which embeds the secret bot token.
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{BASE}/bot{self.token}/{method}",
                    json=payload or {},
                )
                http_status = response.status_code
                try:
                    result = response.json()
                except ValueError:
                    raise RuntimeError(
                        f"Zalo {method}: invalid JSON, HTTP {http_status}"
                    ) from None
        except httpx.RequestError:
            # Suppress exception chaining so the raw tokenized URL cannot leak.
            raise RuntimeError(f"Zalo {method}: network request failed") from None

        if not isinstance(result, dict):
            raise RuntimeError(f"Zalo {method}: unexpected JSON type")

        code = result.get("error_code")
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = None

        # Some Zalo Bot installations return HTTP 200 + ok=false + 408 when
        # long polling expires without any incoming messages. This is a normal
        # empty poll, not a need to keep logging a stack trace every 25s.
        if method == "getUpdates" and http_status == 200 and code_int == 408:
            return {"ok": True, "result": []}

        if http_status >= 400 or result.get("ok") is not True:
            reason = _safe_description(result.get("description") or result.get("message"), self.token)
            raise RuntimeError(
                f"Zalo {method} rejected: HTTP={http_status}, "
                f"error_code={code_int if code_int is not None else 'unknown'}, "
                f"description={reason}"
            )
        return result

    def send_message(self, chat_id: str, text: str) -> dict:
        if self.mock:
            return {"ok": True, "mock": True, "chat_id": chat_id, "text": text}
        # Zalo Bot API supports at most 2000 chars per message; use a hard cap.
        return self.call("sendMessage", {"chat_id": chat_id, "text": text[:1995]})

"""Set, inspect, or delete official Zalo Bot webhook (no unofficial personal login)."""
import argparse
from app.config import settings
from app.zalo import ZaloClient

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["set", "info", "delete", "me", "test"])
parser.add_argument("--url", help="Public HTTPS endpoint, e.g. https://xxx.azurecontainerapps.io/webhooks/zalo")
args = parser.parse_args()
if not settings.bot_token:
    raise SystemExit("Missing BOT_TOKEN")
client = ZaloClient(mock=False)
methods = {"set": "setWebhook", "info": "getWebhookInfo", "delete": "deleteWebhook", "me": "getMe", "test": "testWebhook"}
body = {}
if args.action == "set":
    if not args.url or not args.url.startswith("https://") or len(settings.webhook_secret) < 8 or settings.webhook_secret.startswith("replace-"):
        raise SystemExit("Need --url HTTPS and WEBHOOK_SECRET >=8 characters")
    body = {"url": args.url, "secret_token": settings.webhook_secret}
print(client.call(methods[args.action], body))

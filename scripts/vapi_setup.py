"""Creates or updates the Vapi assistant from vapi/assistant.json +
app/prompts/agent.md.

    python scripts/vapi_setup.py

Requires in .env:
    VAPI_PRIVATE_KEY     Vapi dashboard -> API Keys -> Private
    PUBLIC_BASE_URL      https://your-app.vercel.app  (no trailing slash)
    VAPI_WEBHOOK_SECRET

If VAPI_ASSISTANT_ID is set the existing assistant is patched, otherwise a new
one is created and its id is printed for you to save.

Everything this script does can also be done by hand in the Vapi dashboard -
see README, "Configuring the voice agent".
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv()

private_key = os.environ.get("VAPI_PRIVATE_KEY")
base_url = os.environ.get("PUBLIC_BASE_URL")
secret = os.environ.get("VAPI_WEBHOOK_SECRET")
assistant_id = os.environ.get("VAPI_ASSISTANT_ID")

missing = [name for name, value in (
    ("VAPI_PRIVATE_KEY", private_key),
    ("PUBLIC_BASE_URL", base_url),
    ("VAPI_WEBHOOK_SECRET", secret),
) if not value]
if missing:
    print(f"Missing required env var(s): {', '.join(missing)}")
    sys.exit("Fill them into .env, then run this script again.")


def to_system_prompt(markdown: str) -> str:
    """Strip the editorial header and HTML comments; send only the prompt itself."""
    parts = re.split(r"^---$", markdown, flags=re.MULTILINE)
    body = "---".join(parts[1:]) if len(parts) > 1 else markdown
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


raw_config = (ROOT / "vapi" / "assistant.json").read_text(encoding="utf8")
prompt = to_system_prompt((ROOT / "app" / "prompts" / "agent.md").read_text(encoding="utf8"))
server_url = f"{base_url.rstrip('/')}/webhook/vapi"

config = json.loads(
    raw_config
    .replace('"{{SYSTEM_PROMPT}}"', json.dumps(prompt))
    .replace("{{SERVER_URL}}", server_url)
    .replace("{{SERVER_SECRET}}", secret)
)
config.pop("_comment", None)

is_update = bool(assistant_id)
url = f"https://api.vapi.ai/assistant/{assistant_id}" if is_update else "https://api.vapi.ai/assistant"

request = urllib.request.Request(
    url,
    data=json.dumps(config).encode("utf8"),
    method="PATCH" if is_update else "POST",
    headers={
        "Authorization": f"Bearer {private_key}",
        "Content-Type": "application/json",
        # The API sits behind a CDN that rejects the default urllib user agent
        # with a 403 (error 1010).
        "User-Agent": "carecloud-patient-intake/1.0",
    },
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
except urllib.error.HTTPError as exc:
    print(f"Vapi returned {exc.code}:")
    print(exc.read().decode("utf8", "replace"))
    print("\nIf this is a schema complaint, configure the assistant in the")
    sys.exit('Vapi dashboard instead - see README, "Configuring the voice agent".')

print(f"Assistant {'updated' if is_update else 'created'}: {payload['id']}")
print(f"Webhook   : {server_url}")
print(f"Prompt    : {len(prompt)} chars from app/prompts/agent.md")
if not is_update:
    print("\nAdd this to .env so future runs update instead of duplicating:")
    print(f"VAPI_ASSISTANT_ID={payload['id']}")

"""Cheap XAI_API_KEY ping. Sends a tiny chat to grok-4-fast and prints the result."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from xai_sdk import Client
from xai_sdk.chat import user


def main() -> None:
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise SystemExit("XAI_API_KEY is missing from environment / .env")

    client = Client(api_key=api_key, timeout=60.0)
    chat = client.chat.create(model="grok-4-fast")
    chat.append(user("Reply with exactly the word: pong"))
    response = chat.sample()
    print("Model:", getattr(response, "model", "?"))
    print("Content:", (response.content or "").strip())


if __name__ == "__main__":
    main()

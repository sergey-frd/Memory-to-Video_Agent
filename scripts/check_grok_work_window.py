from __future__ import annotations

import json
import sys
from urllib.error import URLError
from urllib.request import urlopen


def _is_grok_url(url: str) -> bool:
    normalized = url.casefold()
    return (
        normalized.startswith("https://grok.com")
        or normalized.startswith("https://www.grok.com")
        or "://x.com/i/grok" in normalized
    )


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "9222"
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            targets = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        print(f"Grok work-window Chrome is not reachable on port {port}: {exc}")
        return 1

    pages = [target for target in targets if target.get("type") == "page"]
    grok_pages = [target for target in pages if _is_grok_url(str(target.get("url") or ""))]
    if grok_pages:
        title = grok_pages[0].get("title") or "(untitled)"
        url = grok_pages[0].get("url") or ""
        print(f"Grok work-window page found: {title} - {url}")
        return 0

    print(f"No Grok page is visible on Chrome debug port {port}.")
    if pages:
        print("Visible pages on this debug port:")
        for target in pages:
            title = target.get("title") or "(untitled)"
            url = target.get("url") or ""
            print(f"  - {title} - {url}")
    else:
        print("Visible pages on this debug port: none.")
    print("Open https://grok.com/imagine in the reusable Chrome window that is listening on this port, then run the batch again.")
    print("If you are watching another Chrome window, that window is not the one exposed through this debug port.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

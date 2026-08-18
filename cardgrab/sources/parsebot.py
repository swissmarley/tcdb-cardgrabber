"""Client for the unofficial TCDB API on parse.bot.

This is the ONLY module in the package that touches the network. The pipeline
itself stays offline: this client fetches a checklist and writes it to a local
manifest file, which the offline pipeline then consumes like any other source.

The API supplies card metadata only -- number, name, team, notations. It does
not return image URLs or image data, so it cannot be used to download card
images. See fetch_checklist() for what it does provide.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cardgrab.models import CardRef

BASE_URL = "https://api.parse.bot/scraper/123aeda8-4611-4871-a592-2109a3f6434f"
API_KEY_ENV = "PARSE_API_KEY"
TIMEOUT = 60

# Credit cost per call, from the published pricing. Shown before spending.
COSTS = {"list_sets": 1, "get_checklist": 3, "get_set_details": 10, "search_cards": 1}


class ParseBotError(Exception):
    """Raised when the API call fails or returns something unusable."""


def api_key(explicit: str | None = None) -> str:
    """Resolve the API key from the argument or the environment."""
    key = explicit or os.environ.get(API_KEY_ENV, "")
    if not key:
        raise ParseBotError(
            f"No API key. Set it with:\n"
            f"  export {API_KEY_ENV}='your-key-here'\n"
            f"Get a key by signing up at https://parse.bot"
        )
    return key


def call(endpoint: str, params: dict[str, str], key: str) -> dict:
    """Make one API request and return the decoded payload."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    url = f"{BASE_URL}/{endpoint}?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "X-API-Key": key,
            "Accept": "application/json",
            "User-Agent": "cardgrab/1.1",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ParseBotError(_explain_http(exc, endpoint)) from exc
    except urllib.error.URLError as exc:
        raise ParseBotError(f"cannot reach the API: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ParseBotError(f"{endpoint} timed out after {TIMEOUT}s") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ParseBotError(f"{endpoint} returned invalid JSON: {body[:200]}") from exc

    if isinstance(payload, dict) and payload.get("status") == "error":
        raise ParseBotError(f"{endpoint} failed: {payload.get('message', payload)}")

    return payload


def _explain_http(exc: urllib.error.HTTPError, endpoint: str) -> str:
    """Turn an HTTP status into something the user can act on."""
    known = {
        401: "API key rejected. Check PARSE_API_KEY is correct.",
        403: "API key lacks access to this endpoint.",
        402: "Out of credits. Top up your parse.bot account.",
        429: "Rate limited. Wait a moment and try again.",
        500: "The API had a server error. Try again shortly.",
        502: "The API could not reach tcdb.com. Try again shortly.",
    }
    hint = known.get(exc.code, f"HTTP {exc.code}")
    return f"{endpoint}: {hint}"


def _unwrap(payload: dict, field: str) -> list:
    """Pull a list field out of the response, tolerating the data wrapper."""
    if not isinstance(payload, dict):
        raise ParseBotError(f"expected an object, got {type(payload).__name__}")

    for container in (payload.get("data"), payload):
        if isinstance(container, dict) and isinstance(container.get(field), list):
            return container[field]

    raise ParseBotError(
        f"response has no '{field}' list. Keys seen: {sorted(payload)[:8]}"
    )


def list_sets(
    year: str, sport: str = "", query: str = "", key: str | None = None
) -> list[dict]:
    """Find sets for a year, so you can get a set's sid."""
    payload = call("list_sets", {"year": year, "sport": sport, "query": query},
                   api_key(key))
    return _unwrap(payload, "sets")


def get_checklist(sid: str, key: str | None = None) -> list[CardRef]:
    """Fetch every card in a set. Metadata only -- no images."""
    payload = call("get_checklist", {"sid": sid}, api_key(key))
    raw = _unwrap(payload, "cards")

    cards: dict[str, CardRef] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        number = str(entry.get("number", "")).strip()
        if not number:
            continue
        cards.setdefault(
            number,
            CardRef(number=number, name=str(entry.get("name", "")).strip()),
        )

    if not cards:
        raise ParseBotError(f"set {sid} returned no usable cards")

    return sorted(cards.values(), key=lambda c: c.sort_key)


def fetch_checklist(sid: str, destination: Path, key: str | None = None) -> int:
    """Fetch a checklist and write it as a manifest the pipeline can read.

    The manifest carries no image URLs, because the API does not supply any.
    Images must still come from the browser.
    """
    cards = get_checklist(sid, key)
    payload = {
        "source_url": f"{BASE_URL}/get_checklist?sid={sid}",
        "source": "parse.bot TCDB API",
        "note": "Checklist metadata only. This API returns no image URLs.",
        "cards": [{"number": c.number, "name": c.name} for c in cards],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return len(cards)

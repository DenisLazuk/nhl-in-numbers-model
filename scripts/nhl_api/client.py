"""HTTP client with file cache for NHL public APIs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (NHL Analytics App v0.1)"}
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
API_CACHE_DIR = DATA_DIR / "api_cache"

API_WEB = "https://api-web.nhle.com/v1"
STATS_REST = "https://api.nhle.com/stats/rest/en"


def get_json(url: str, params: dict | None = None, use_cache: bool = True) -> dict:
    """GET JSON. A finished snapshot stays cached; a live refetch replaces the file."""
    key = hashlib.md5(f"{url}|{json.dumps(params, sort_keys=True)}".encode()).hexdigest()
    cache = API_CACHE_DIR / f"{key}.json"
    if use_cache and cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache.unlink()  # битый/пустой кэш — перезапросить
    r = requests.get(url, params=params, headers=UA, timeout=60)
    r.raise_for_status()
    data = r.json()
    API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data

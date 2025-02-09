"""Resilient HTTP client with retry, backoff and raw landing-zone capture.

Every external response is written to ``data/raw`` before any parsing, so the
pipeline is reproducible and auditable (source fidelity layer).
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from config import (
    LOGS,
    RAW,
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
    USER_AGENT,
)


class FetchError(RuntimeError):
    """Raised when a source cannot be retrieved after all retries."""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    retry = HTTPAdapter(
        max_retries=0, pool_connections=8, pool_maxsize=8
    )
    s.mount("https://", retry)
    s.mount("http://", retry)
    return s


def _raw_path(source: str, name: str) -> object:

    folder = RAW / source
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fetch(
    url: str,
    source: str,
    name: str,
    params: dict[str, Any] | None = None,
    force: bool = False,
) -> bytes | None:
    """Download ``url`` into the raw landing zone and return the payload.

    Returns ``None`` when the source is already cached and ``force`` is False.
    Failures are non-fatal: they return ``None`` so a single unreachable
    source cannot abort the whole pipeline.
    """
    target = _raw_path(source, name)
    if target.exists() and not force:
        return target.read_bytes()

    sess = _session()
    last_exc: Exception | None = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            resp = sess.get(
                url, params=params, timeout=REQUEST_TIMEOUT
            )
            if resp.status_code == 200:
                payload = resp.content
                _write_atomically(target, payload)
                _log(
                    source,
                    "OK",
                    url,
                    len(payload),
                    sha256_bytes(payload)[:16],
                )
                return payload
            last_exc = HTTPError(resp.status_code, resp.text[:200])
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(min(2 ** attempt, 8))

    _log(source, "FAIL", url, 0, str(last_exc)[:120])
    return None


class HTTPError(Exception):
    def __init__(self, code: int, text: str) -> None:
        super().__init__(f"HTTP {code}: {text}")
        self.code = code


def _write_atomically(path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(path)


def fetch_json(url: str, source: str, name: str, **kw) -> Any | None:
    payload = fetch(url, source, name, **kw)
    if payload is None:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        _log(source, "BADJSON", name, 0, str(exc)[:120])
        return None


def log_jsonl(name: str, payload: dict) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / name
    payload = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        **payload,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _log(source: str, status: str, url: str, nbytes: int, note: str) -> None:
    log_jsonl(
        "ingestion.jsonl",
        {
            "source": source,
            "status": status,
            "url": url[:300],
            "bytes": nbytes,
            "note": note,
        },
    )
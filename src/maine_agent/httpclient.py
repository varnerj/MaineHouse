"""Minimal stdlib-only HTTP helper (no third-party dependencies)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class HttpError(RuntimeError):
    def __init__(self, url, status, body=""):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.url = url
        self.status = status
        self.body = body


def _request(method, url, params=None, data=None, headers=None, retries=2):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req_headers = {"User-Agent": config.USER_AGENT}
    req_headers.update(headers or {})
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)

    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT_S) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = HttpError(url, e.code, e.read().decode("utf-8", errors="replace"))
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_err
        except urllib.error.URLError as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_err


def get(url, params=None, headers=None, retries=2):
    return _request("GET", url, params=params, headers=headers, retries=retries)


def get_json(url, params=None, headers=None, retries=2):
    status, body = get(url, params=params, headers=headers, retries=retries)
    return json.loads(body)


def post_form(url, data=None, headers=None, retries=2):
    return _request("POST", url, data=data, headers=headers, retries=retries)


def post_form_json(url, data=None, headers=None, retries=2):
    status, body = post_form(url, data=data, headers=headers, retries=retries)
    return json.loads(body)

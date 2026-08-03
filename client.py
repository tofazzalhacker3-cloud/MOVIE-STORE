#!/usr/bin/env python3
"""
MovieBox API client - reverse engineered from MovieBoxProvider.1609710508.cs3
Handles: signatures (x-client-token / x-tr-signature), bearer token, all endpoints.
"""
import hashlib
import hmac
import base64
import json
import os
import time
import requests
from urllib.parse import urlsplit, parse_qsl
from collections import defaultdict

BASE_URL = "https://api3.aoneroom.com"

# Double base64-decode is required (same as the dex does)
SECRET_KEY = base64.b64decode(base64.b64decode("NzZpUmwwN3MweFNOOWpxbUVXQXQ3OUVCSlp1bElRSXNWNjRGWnIyTw=="))
SECRET_KEY_ALT = base64.b64decode(base64.b64decode("WHFuMm5uTzQxL0w5Mm8xaXVYaFNMSFRiWHZZNFo1Wlo2Mm04bVNMQQ=="))

USER_AGENT = (
    "com.community.oneroom/50020088 "
    "(Linux; U; Android 13; en_US; SM-S918B; Build/TQ3A.230901.001; Cronet/145.0.7582.0)"
)

DEVICE_ID = os.urandom(16).hex()

def client_info():
    return (
        '{"package_name":"com.community.oneroom","version_name":"3.0.13.0325.03",'
        '"version_code":50020088,"os":"android","os_version":"13","install_ch":"ps",'
        f'"device_id":"{DEVICE_ID}","install_store":"ps",'
        '"gaid":"1b2212c1-dadf-43c3-a0c8-bd6ce48ae22d","brand":"Samsung","model":"SM-S918B",'
        '"system_language":"en","net":"NETWORK_WIFI","region":"US",'
        '"timezone":"Asia/Calcutta","sp_code":""}'
    )


def md5hex(data):
    return hashlib.md5(data).hexdigest()


def x_client_token():
    ts = str(int(time.time() * 1000))
    return ts + "," + md5hex(ts[::-1].encode("utf-8"))


def canonical_string(method, accept, content_type, url, body, timestamp):
    parts = urlsplit(url)
    grouped = defaultdict(list)
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        grouped[key].append(value)
    query = "&".join(
        "&".join(f"{key}={value}" for value in grouped[key])
        for key in sorted(grouped)
    )
    canonical_url = (parts.path + "?" + query) if query else parts.path
    if body is not None:
        body_bytes = body.encode("utf-8")
        body_hash = md5hex(body_bytes[:102400])
        body_length = str(len(body_bytes))
    else:
        body_hash, body_length = "", ""
    return (
        f"{method.upper()}\n{accept or ''}\n{content_type or ''}\n{body_length}\n"
        f"{timestamp}\n{body_hash}\n{canonical_url}"
    )


def x_tr_signature(method, accept, content_type, url, body=None, use_alt=False):
    timestamp = str(int(time.time() * 1000))
    canonical = canonical_string(method, accept, content_type, url, body, timestamp)
    secret = SECRET_KEY_ALT if use_alt else SECRET_KEY
    digest = hmac.new(secret, canonical.encode("utf-8"), hashlib.md5).digest()
    return timestamp + "|2|" + base64.b64encode(digest).decode("utf-8")


class MovieBoxClient:
    """Session wrapper with cached bearer token."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["Accept-Encoding"] = "gzip, deflate"
        self.bearer_token = None
        self.token_expiry = 0

    def _refresh_token(self):
        url = BASE_URL + "/wefeed-mobile-bff/tab/ranking-list?tabId=0&categoryType=4516404531735022304&page=1&perPage=1"
        headers = {
            "user-agent": USER_AGENT,
            "accept": "application/json",
            "content-type": "application/json",
            "connection": "keep-alive",
            "x-client-token": x_client_token(),
            "x-tr-signature": x_tr_signature("GET", "application/json", "application/json", url),
            "x-client-info": client_info(),
            "x-client-status": "0",
        }
        last_err = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, headers=headers, timeout=25)
                break
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_err = exc
                time.sleep(0.5 * (attempt + 1))
        else:
            raise RuntimeError(f"token ping failed: {last_err}")
        if resp.status_code != 200:
            raise RuntimeError(f"token ping failed: HTTP {resp.status_code} {resp.text[:200]}")
        x_user = resp.headers.get("x-user")
        if not x_user:
            raise RuntimeError("token ping returned no x-user header")
        payload = json.loads(x_user)
        self.bearer_token = payload.get("token")
        exp = payload.get("exp") or 0
        self.token_expiry = exp - 60
        if not self.bearer_token:
            raise RuntimeError("x-user header had no token")

    def _ensure_token(self):
        if not self.bearer_token or time.time() >= self.token_expiry:
            self._refresh_token()

    def request(self, method, path, params=None, body=None, use_alt=False, extra_headers=None, retries=4):
        last_err = None
        for attempt in range(retries):
            try:
                url = BASE_URL + path
                if params:
                    url = url + ("&" if "?" in url else "?") + "&".join(
                        f"{k}={v}" for k, v in params.items()
                    )
                self._ensure_token()
                content_type = "application/json; charset=utf-8" if method == "POST" and body else "application/json"
                headers = {
                    "user-agent": USER_AGENT,
                    "accept": "application/json",
                    "content-type": content_type,
                    "connection": "keep-alive",
                    "x-client-token": x_client_token(),
                    "x-tr-signature": x_tr_signature(method, "application/json", content_type, url, body, use_alt),
                    "x-client-info": client_info(),
                    "x-client-status": "0",
                    "Authorization": "Bearer " + self.bearer_token,
                }
                if extra_headers:
                    headers.update(extra_headers)
                if method == "POST":
                    resp = self.session.post(url, data=body, headers=headers, timeout=25)
                else:
                    resp = self.session.get(url, headers=headers, timeout=25)
                if resp.status_code == 403:
                    raise RuntimeError(f"upstream HTTP 403 {resp.text[:200]}")
                if resp.status_code == 401:
                    self.bearer_token = None
                    self.token_expiry = 0
                    raise RuntimeError("token expired")
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"upstream HTTP {resp.status_code}")
                try:
                    return resp.json()
                except ValueError:
                    return {"code": resp.status_code, "message": "non-json response", "raw": resp.text[:500]}
            except (requests.ConnectionError, requests.Timeout, RuntimeError) as exc:
                last_err = exc
                if "HTTP 403" in str(exc) or "non-json" in str(exc):
                    raise
                time.sleep(0.6 * (attempt + 1))
        raise RuntimeError(str(last_err) or "request failed")


client = MovieBoxClient()

#!/usr/bin/env python3
"""
MovieBox Flask API
Developer: Tofazzal Hossain
"""
import base64
import json
import os
import re
import time
import urllib.parse
import requests
from flask import Flask, jsonify, render_template, request, Response, stream_with_context
from client import client, BASE_URL, USER_AGENT

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
app.json.sort_keys = False
app.json.ensure_ascii = False

DEVELOPER = "Tofazzal Hossain"

CATEGORIES = {
    "Trending": "4516404531735022304",
    "Trending in Cinema": "5692654647815587592",
    "Bollywood": "414907768299210008",
    "South Indian": "3859721901924910512",
    "Hollywood": "8019599703232971616",
    "Top Series This Week": "4741626294545400336",
    "Anime": "8434602210994128512",
    "Reality TV": "1255898847918934600",
    "Indian Drama": "4903182713986896328",
    "Korean Drama": "7878715743607948784",
    "Chinese Drama": "8788126208987989488",
    "Western TV": "3910636007619709856",
    "Turkish Drama": "5177200225164885656",
    "Movies (All)": "1|1",
    "Series (All)": "1|2",
    "Anime (All)": "1|1006",
    "Indian Movies": "1|1;country=India",
    "Indian Series": "1|2;country=India",
    "USA Movies": "1|1;classify=Hindi dub;country=United States",
    "USA Series": "1|2;classify=Hindi dub;country=United States",
    "Action Movies": "1|1;classify=Hindi dub;genre=Action",
    "Comedy Movies": "1|1;classify=Hindi dub;genre=Comedy",
    "Crime Movies": "1|1;classify=Hindi dub;genre=Crime",
    "Romance Movies": "1|1;classify=Hindi dub;genre=Romance",
    "Romance Series": "1|2;classify=Hindi dub;genre=Romance",
}


def wrap(data, status=200):
    return jsonify({"developer": DEVELOPER, "code": 0, "message": "ok", "data": data}), status


def error(message, status=500):
    return jsonify({"developer": DEVELOPER, "code": status, "message": message, "data": None}), status


def proxied(url, cookie="", safe=""):
    return f"/proxy?url={urllib.parse.quote(url, safe=safe)}&cookie={urllib.parse.quote(cookie or '', safe='')}"


def pick_subjects(data, key="results"):
    out = []
    d = data.get("data") if isinstance(data, dict) else None
    if not isinstance(d, dict):
        return out
    raw = d.get(key, []) or []
    if raw and isinstance(raw[0], dict) and "subjects" in raw[0]:
        for res in raw:
            out.extend(res.get("subjects", []) or [])
    elif isinstance(raw, list):
        out.extend(raw)
    out.extend(d.get("list", []) or [])
    return out


def cover_of(item):
    cover = item.get("cover") or item.get("coverUrl") or ""
    if isinstance(cover, dict):
        cover = cover.get("url", "")
    if cover and not str(cover).startswith("http"):
        cover = "https://image.tmdb.org/t/p/w500" + cover
    return cover


def brief(item):
    return {
        "id": item.get("subjectId") or item.get("id"),
        "title": item.get("title") or item.get("name", ""),
        "type": item.get("subjectType"),
        "coverUrl": cover_of(item),
        "imdbRating": item.get("imdbRatingValue") or item.get("imdbRate") or item.get("rate"),
        "genre": item.get("genre", ""),
        "language": item.get("language", ""),
        "releaseDate": item.get("releaseDate", ""),
        "duration": item.get("duration", ""),
        "hasResource": item.get("hasResource"),
    }


@app.route("/")
def index():
    if wants_html():
        return render_template("index.html")
    return wrap({
        "name": "MovieBox API",
        "version": "1.0.0",
        "base_url": BASE_URL,
        "developer": DEVELOPER,
        "endpoints": {
            "GET /app.json": "App metadata",
            "GET /categories": "Home categories",
            "GET /home?category=<id>&page=<n>&perPage=<n>": "Ranking list",
            "GET /list?channelId=1|1&page=<n>&perPage=<n>&country=<c>&genre=<g>": "Filtered list",
            "GET /search?q=<query>&page=<n>": "Search",
            "GET /details/<subjectId>": "Movie/Series details",
            "GET /seasons/<subjectId>": "Season info",
            "GET /dub-info/<subjectId>": "Dub versions",
            "GET /streams/<subjectId>?se=<s>&ep=<e>": "Play streams",
            "GET /resource/<subjectId>?se=<s>&epFrom=<f>&epTo=<t>&resolution=<r>": "Download resources",
            "GET /captions/<subjectId>?streamId=<id>": "Subtitles",
            "GET /proxy?url=&cookie=": "Stream proxy (manifest rewrite)",
        },
    })


def wants_html():
    return request.accept_mimetypes.best_match(["text/html", "application/json"]) == "text/html"


@app.route("/browse")
def browse_page():
    return render_template("browse.html", categories=CATEGORIES)


@app.route("/watch/<subject_id>")
def watch_page(subject_id):
    return render_template("watch.html", subject_id=subject_id)


@app.route("/proxy")
def proxy():
    url = request.args.get("url", "")
    cookie = request.args.get("cookie", "")
    if not url:
        return error("missing url", 400)
    headers = {"User-Agent": "ExoPlayer/2.19.1 (Linux;Android 13)"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        upstream = requests.get(url, headers=headers, stream=True, timeout=30)
    except Exception as exc:
        return error(f"proxy failed: {exc}", 502)
    ct = upstream.headers.get("content-type", "application/octet-stream")
    if upstream.status_code >= 400:
        return Response(upstream.text, status=upstream.status_code, content_type="text/plain")
    body = upstream.content
    low_path = urllib.parse.urlsplit(url).path.lower()
    if low_path.endswith(".mpd") or "dash" in ct:
        text = _rewrite_mpd(body.decode("utf-8", "replace"), url, cookie)
        body = text.encode()
        ct = "application/dash+xml"
    elif low_path.endswith(".m3u8") or "mpegurl" in ct:
        text = _rewrite_m3u8(body.decode("utf-8", "replace"), url, cookie)
        body = text.encode()
        ct = "application/vnd.apple.mpegurl"
    resp = Response(stream_with_context((body,)), content_type=ct)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Length"] = str(len(body))
    return resp


@app.route("/download")
def download():
    url = request.args.get("url", "")
    name = request.args.get("name", "video.mp4")
    if not url.startswith(("https://", "http://")):
        return error("invalid url", 400)
    headers = {
        "User-Agent": "okhttp/4.12.0",
        "Connection": "Keep-Alive",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
    }
    rng = request.headers.get("Range")
    if rng:
        headers["Range"] = rng
    try:
        upstream = requests.get(url, headers=headers, stream=True, timeout=60)
    except Exception as exc:
        return error(f"download failed: {exc}", 502)
    if upstream.status_code >= 400:
        return error(f"upstream {upstream.status_code}", 502)
    resp = Response(
        stream_with_context(upstream.iter_content(chunk_size=64 * 1024)),
        status=upstream.status_code,
    )
    resp.headers["Content-Type"] = upstream.headers.get("content-type", "application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    resp.headers["Content-Length"] = upstream.headers.get("content-length", "")
    if upstream.headers.get("content-range"):
        resp.headers["Content-Range"] = upstream.headers["content-range"]
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/cap")
def cap():
    url = request.args.get("url", "")
    if not url.startswith(("https://", "http://")):
        return error("invalid url", 400)
    try:
        upstream = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    except Exception as exc:
        return error(f"captions failed: {exc}", 502)
    if upstream.status_code >= 400:
        return error(f"upstream {upstream.status_code}", 502)
    text = upstream.content.decode("utf-8", "replace").lstrip("\ufeff")
    if "WEBVTT" in text[:100]:
        body = text.encode()
    else:
        body = ("WEBVTT\n\n" + re.sub(
            r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text
        )).encode("utf-8")
    resp = Response(body, content_type="text/vtt; charset=utf-8")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def _rewrite_mpd(text, base_url, cookie):
    base_dir = base_url[: base_url.rfind("/")]
    def wrap(u, template=False):
        u = u.strip()
        if not u:
            return u
        target = u if u.startswith("http") else base_dir + "/" + u.lstrip("/")
        return proxied(target, cookie, safe="$%" if template else "")
    text = re.sub(r"(<BaseURL>)([^<]+)(</BaseURL>)",
                  lambda m: m.group(1) + wrap(m.group(2)) + m.group(3), text)
    def attr(m):
        val = m.group(2)
        return m.group(1) + wrap(val, template="$" in val) + '"'
    text = re.sub(r'(initialization="|media=")([^"]+)"', attr, text)
    return text


def _rewrite_m3u8(text, base_url, cookie):
    base_dir = base_url[: base_url.rfind("/")]
    out = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            out.append(line)
            continue
        u = line if line.startswith("http") else base_dir + "/" + line.lstrip("/")
        out.append(proxied(u, cookie))
    return "\n".join(out)


@app.route("/app.json")
def app_json():
    return wrap({
        "name": "MovieBox API",
        "version": "1.0.0",
        "developer": DEVELOPER,
        "author": DEVELOPER,
        "stack": "Flask",
        "repository": None,
        "endpoints": list(CATEGORIES.keys()),
    })


@app.route("/categories")
def categories():
    return wrap(CATEGORIES)


@app.route("/home")
def home():
    category = request.args.get("category", CATEGORIES["Trending"])
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("perPage", 20, type=int)
    try:
        data = client.request("GET", "/wefeed-mobile-bff/tab/ranking-list",
                              {"tabId": 0, "categoryType": category, "page": page, "perPage": per_page})
    except Exception as exc:
        return error(str(exc))
    items = [brief(i) for i in pick_subjects(data, "subjects")]
    d = data.get("data") if isinstance(data, dict) else None
    pager = d.get("pager") if isinstance(d, dict) else None
    return wrap({"category": category, "page": page, "items": items, "pager": pager})


@app.route("/list")
def list_items():
    channel = request.args.get("channelId", "1|1")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("perPage", 20, type=int)
    classify = request.args.get("classify", "All")
    country = request.args.get("country", "All")
    year = request.args.get("year", "All")
    genre = request.args.get("genre", "All")
    sort = request.args.get("sort", "ForYou")
    body = json.dumps({
        "page": page, "perPage": per_page, "channelId": channel,
        "classify": classify, "country": country, "year": year,
        "genre": genre, "sort": sort,
    }, separators=(",", ":"))
    d = None
    last_err = None
    for attempt in range(3):
        try:
            data = client.request("POST", "/wefeed-mobile-bff/subject-api/list", body=body)
            d = data.get("data") if isinstance(data, dict) else None
            if isinstance(d, dict) and d.get("items"):
                break
            last_err = None
        except Exception as exc:
            last_err = exc
        time.sleep(0.8)
    if last_err is not None:
        return error(str(last_err))
    if not isinstance(d, dict):
        d = {}
    items = [brief(i) for i in (d.get("items") or []) if isinstance(i, dict)]
    return wrap({"channelId": channel, "page": page, "items": items, "pager": d.get("pager")})


@app.route("/search")
def search():
    query = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    if wants_html():
        return render_template("search.html", query=query)
    if not query:
        return error("Missing 'q' parameter", 400)
    body = json.dumps({"page": page, "perPage": 20, "keyword": query}, separators=(",", ":"))
    try:
        data = client.request("POST", "/wefeed-mobile-bff/subject-api/search/v2", body=body)
    except Exception as exc:
        return error(str(exc))
    items = [brief(i) for i in pick_subjects(data, "results")]
    return wrap({"query": query, "page": page, "items": items})


@app.route("/details/<subject_id>")
def details(subject_id):
    if wants_html():
        return render_template("details.html", subject_id=subject_id)
    try:
        data = client.request("GET", "/wefeed-mobile-bff/subject-api/get",
                              {"subjectId": subject_id})
    except Exception as exc:
        return error(str(exc))
    d = data.get("data") if isinstance(data, dict) else None
    if not isinstance(d, dict):
        return error("Content not found", 404)
    item = brief(d)
    item["description"] = d.get("description", "")
    item["backgroundUrl"] = cover_of({"coverUrl": d.get("background") or d.get("backgroundUrl") or ""})
    item["staffList"] = d.get("staffList", [])
    item["tags"] = d.get("tags", [])
    item["videos"] = d.get("videos", [])
    item["dubs"] = [{"subjectId": x.get("subjectId"), "lanName": x.get("lanName"),
                     "lanCode": x.get("lanCode"), "original": x.get("original")}
                    for x in (d.get("dubs") or []) if isinstance(x, dict)]
    return wrap(item)


@app.route("/seasons/<subject_id>")
def seasons(subject_id):
    try:
        data = client.request("GET", "/wefeed-mobile-bff/subject-api/season-info",
                              {"subjectId": subject_id})
    except Exception as exc:
        return error(str(exc))
    d = data.get("data") if isinstance(data, dict) else {}
    return wrap(d)


@app.route("/dub-info/<subject_id>")
def dub_info(subject_id):
    try:
        data = client.request("GET", "/wefeed-mobile-bff/subject-api/dub-info",
                              {"subjectId": subject_id})
    except Exception as exc:
        return error(str(exc))
    d = data.get("data") if isinstance(data, dict) else {}
    return wrap(d.get("dubs", []))


@app.route("/streams/<subject_id>")
def streams(subject_id):
    se = request.args.get("se", "0")
    ep = request.args.get("ep", "0")
    try:
        data = client.request("GET", "/wefeed-mobile-bff/subject-api/play-info",
                              {"subjectId": subject_id, "se": se, "ep": ep})
    except Exception as exc:
        return error(str(exc))
    d = data.get("data") if isinstance(data, dict) else {}
    return wrap({"subjectId": subject_id, "se": se, "ep": ep, "streams": d.get("streams", [])})


@app.route("/resource/<subject_id>")
def resource(subject_id):
    se = request.args.get("se", "1")
    ep_from = request.args.get("epFrom", "1")
    ep_to = request.args.get("epTo", "1")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("perPage", 20, type=int)
    all_flag = request.args.get("all", "1")
    resolution = request.args.get("resolution", "0")
    try:
        data = client.request("GET", "/wefeed-mobile-bff/subject-api/resource", {
            "subjectId": subject_id, "page": page, "perPage": per_page, "all": all_flag,
            "startPosition": 1, "endPosition": 1, "pagerMode": 0,
            "resolution": resolution, "se": se, "epFrom": ep_from, "epTo": ep_to,
        })
    except Exception as exc:
        return error(str(exc))
    d = data.get("data") if isinstance(data, dict) else {}
    return wrap({"subjectId": subject_id, "pager": d.get("pager"), "resources": d.get("list", [])})


@app.route("/resources-all/<subject_id>")
def resources_all(subject_id):
    items, page = [], 1
    while page <= 30:
        try:
            data = client.request("GET", "/wefeed-mobile-bff/subject-api/resource", {
                "subjectId": subject_id, "page": page, "perPage": 20, "all": "1",
                "startPosition": 1, "endPosition": 1, "pagerMode": 0,
                "resolution": "0", "se": 1, "epFrom": 1, "epTo": 1,
            })
        except Exception as exc:
            return error(str(exc))
        d = data.get("data") if isinstance(data, dict) else {}
        batch = d.get("list", [])
        items += batch
        pager = d.get("pager") or {}
        if not batch or not pager.get("hasMore"):
            break
        page += 1
    out = []
    for r in items:
        ep_global = r.get("episode") or 0
        se = r.get("se") or (ep_global // 100 if ep_global >= 100 else 1)
        ep = int(ep_global % 100) if ep_global >= 100 else int(ep_global)
        out.append({
            "se": int(se or 1), "ep": ep, "resolution": r.get("resolution"),
            "codecName": r.get("codecName"), "size": r.get("size"),
            "resourceLink": r.get("resourceLink"), "title": r.get("title") or "",
            "duration": r.get("duration"),
        })
    out.sort(key=lambda x: (x["se"], x["ep"]))
    return wrap({"subjectId": subject_id, "resources": out})


@app.route("/captions/<subject_id>")
def captions(subject_id):
    stream_id = request.args.get("streamId")
    captions_list = []
    if stream_id:
        try:
            data = client.request("GET", "/wefeed-mobile-bff/subject-api/get-stream-captions",
                                  {"subjectId": subject_id, "streamId": stream_id})
            d = data.get("data") if isinstance(data, dict) else {}
            captions_list += d.get("extCaptions", [])
        except Exception:
            pass
    try:
        data = client.request("GET", "/wefeed-mobile-bff/subject-api/get-ext-captions",
                              {"subjectId": subject_id, "resourceId": stream_id or "0", "episode": 0})
        d = data.get("data") if isinstance(data, dict) else {}
        captions_list += d.get("extCaptions", [])
    except Exception:
        pass
    seen, unique = set(), []
    for cap in captions_list:
        key = cap.get("lanCode") or cap.get("lan") or cap.get("url")
        if key and key not in seen:
            seen.add(key)
            unique.append(cap)
    return wrap(unique)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  MovieBox API - Developer: {DEVELOPER}")
    print(f"  Running on http://0.0.0.0:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)

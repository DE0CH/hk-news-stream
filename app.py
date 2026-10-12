#!/usr/bin/env python3
"""hk-news-stream: a proof of concept of a K-Matrix-style tagged news stream.

Every POLL_SECONDS it reads the section pages of Hong Kong papers, keeps the headlines it hasn't seen, asks a
model on OpenRouter to tag each one (sentiment + monitor/issue/trend), stores them in DATA_DIR/items.json and
serves them as a chat-style page (/) and JSON (/feed.json). Standard library only, one process, no build.

Env: OPENROUTER_API (required), MODEL (default anthropic/claude-haiku-5.5), POLL_SECONDS (900), DATA_DIR
(/data), PORT (8080), BRIEF (what counts as relevant; default below).
"""
import html, json, os, re, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
MODEL = os.environ.get("MODEL", "anthropic/claude-haiku-5.5")
POLL = int(os.environ.get("POLL_SECONDS", "900"))
KEEP = 2000  # items kept on disk
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
BRIEF = os.environ.get("BRIEF", (
    "The readers monitor Hong Kong public affairs for a client in property, construction and finance: "
    "government policy and the Legislative Council, housing and the property market, land supply and the "
    "Northern Metropolis, infrastructure and cross-border links, the economy, banking, insurance and wealth "
    "management, population and labour, and public complaints about government departments."))

lock = threading.Lock()
state = {"items": [], "last_poll": None, "last_error": None}


# ---- sources: each returns [{"source", "section", "title", "url"}] ----

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-HK,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def oriental_daily():
    """東方日報: the home page lists the day's articles of every section as <option value='/content/<section>/odn-…'>"""
    page, out = get("https://orientaldaily.on.cc/"), []
    for path, title in re.findall(r"<option value='(/content/[^']+/odn-[^']+)'>([^<]+)</option>", page):
        section = path.split("/")[2]
        if section in ("要聞港聞", "產經"):
            out.append({"source": "東方日報", "section": section, "title": " ".join(html.unescape(title).split()),
                        "url": "https://orientaldaily.on.cc" + urllib.parse.quote(path)})
    return out


HKEJ_SECTIONS = {"hknews": "政壇脈搏", "investment": "理財投資", "commentary": "信報評論", "property": "地產"}


def hkej():
    """信報: each section page links /dailynews/<section>/article/<id>/<url-encoded title>"""
    out, errors = [], []
    for sec, name in HKEJ_SECTIONS.items():
        try:
            page = get(f"https://www1.hkej.com/dailynews/{sec}")
        except Exception as e:
            errors.append(f"{sec}: {e}")
            continue
        seen = set()
        for aid, slug in re.findall(rf'href="/dailynews/{sec}/article/(\d+)/([^"?#]+)"', page):
            if aid in seen:
                continue
            seen.add(aid)
            title = " ".join(urllib.parse.unquote_plus(slug).split())
            out.append({"source": "信報", "section": name, "title": title,
                        "url": f"https://www1.hkej.com/dailynews/{sec}/article/{aid}"})
        if not seen:
            errors.append(f"{sec}: no articles in {len(page)} bytes")
    if errors and not out:
        raise RuntimeError("; ".join(errors))
    return out


SOURCES = [oriental_daily, hkej]


# ---- tagging ----

PROMPT = """You tag Hong Kong news headlines for a media-monitoring stream.
{brief}

For each headline return:
- "relevant": true if the readers above would want it flagged, else false
- "sentiment": "positive", "neutral" or "negative" (tone toward Hong Kong / the government / the sector)
- "flags": a subset of ["monitor", "issue", "trend"] — monitor = worth watching, issue = a problem or controversy,
  trend = a market or policy trend. Empty when not relevant.

Answer with JSON only: {{"tags": [{{"i": <index>, "relevant": bool, "sentiment": str, "flags": [str]}}, ...]}}

Headlines:
{lines}"""


def tag(items):
    lines = "\n".join(f"{i}. [{it['section']}] {it['title']}" for i, it in enumerate(items))
    body = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT.format(brief=BRIEF, lines=lines)}],
            "response_format": {"type": "json_object"}, "temperature": 0}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API"],
                                          "Content-Type": "application/json", "User-Agent": "hk-news-stream/1",
                                          "X-Title": "hk-news-stream"})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    text = text[text.find("{"):text.rfind("}") + 1]
    for t in json.loads(text).get("tags", []):
        i = t.get("i")
        if isinstance(i, int) and 0 <= i < len(items):
            rel = bool(t.get("relevant"))
            items[i]["sentiment"] = t.get("sentiment") if rel and t.get("sentiment") in ("positive", "neutral", "negative") else None
            items[i]["flags"] = [f for f in t.get("flags", []) if f in ("monitor", "issue", "trend")] if rel else []


# ---- loop ----

def load():
    try:
        with open(os.path.join(DATA_DIR, "items.json")) as f:
            state.update(json.load(f))
    except FileNotFoundError:
        pass


def save():
    tmp = os.path.join(DATA_DIR, "items.json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(DATA_DIR, "items.json"))


def poll_once():
    known = {it["url"] for it in state["items"]}
    fresh, errors = [], []
    counts = {}
    for src in SOURCES:
        try:
            got = src()
            counts[src.__name__] = len(got)
            for it in got:
                if it["url"] not in known:
                    known.add(it["url"])
                    fresh.append(it)
        except Exception as e:
            errors.append(f"{src.__name__}: {e}")
    now = datetime.now(HKT).isoformat(timespec="seconds")
    for i in range(0, len(fresh), 40):
        batch = fresh[i:i + 40]
        try:
            tag(batch)
        except Exception as e:
            errors.append(f"tagging: {e}")
        for it in batch:
            it.setdefault("sentiment", None)
            it.setdefault("flags", [])
            it["seen"] = now
    with lock:
        state["items"] = (state["items"] + fresh)[-KEEP:]
        state["last_poll"] = now
        state["last_error"] = "; ".join(errors) or None
        state["source_counts"] = counts
        save()
    print(f"{now} +{len(fresh)} items{'; ' + state['last_error'] if errors else ''}", flush=True)


def loop():
    while True:
        try:
            poll_once()
        except Exception as e:
            print("poll failed:", e, flush=True)
        time.sleep(POLL)


# ---- web ----

PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), "rb").read()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self.send(200, "text/html; charset=utf-8", PAGE)
        elif path == "/feed.json":
            with lock:
                body = json.dumps({"items": state["items"][-500:], "last_poll": state["last_poll"],
                                   "last_error": state["last_error"],
                                   "source_counts": state.get("source_counts")}, ensure_ascii=False).encode()
            self.send(200, "application/json; charset=utf-8", body)
        elif path == "/healthz":
            self.send(200, "text/plain", b"ok")
        else:
            self.send(404, "text/plain", b"not found")

    def send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    load()
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()

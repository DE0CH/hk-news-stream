# hk-news-stream

A proof of concept of a K-Matrix-style news stream: Hong Kong newspaper headlines, each tagged by a model
with a sentiment (😄 positive · 😐 neutral · 😣 negative) and flags (👀 monitor · ⚠️ issue · 📈 trend),
shown as a chat-style stream.

- `app.py`: one stdlib Python process. Every `POLL_SECONDS` (900) it reads the sources, keeps headlines it
  hasn't seen, tags them in batches through OpenRouter (`MODEL`, default `anthropic/claude-haiku-5.5`), stores
  them in `$DATA_DIR/items.json` and serves `/` (the page), `/feed.json` and `/healthz` on `PORT` (8080).
- `BRIEF` (env) says what counts as relevant; the default is in `app.py`.
- `HKEJ_PROXY` (env, optional): an HTTP proxy for 信報 only, which answers the Jarvis 2 box's IP with 403; the box
  uses an Evomi HK datacenter proxy.
- Sources: 東方日報 (要聞港聞, from the home page) and 信報 (政壇脈搏, 理財投資, 信報評論, 地產).
  discuss.com.hk answers datacenter IPs with 403, so it is not included.

Hosting: the image (`ghcr.io/de0ch/hk-news-stream`, built by `.github/workflows/image.yml`) runs on the
Jarvis 2 box in namespace `jarvis2-edge`, from `k8s/apps/news.yaml` in DE0CH/jarvis2, pinned by digest, at
https://hknews.deyaochen.com/ behind Cloudflare Access. The OpenRouter key is a SOPS Secret there.

Run locally: `OPENROUTER_API=… DATA_DIR=./data python3 app.py`, then open http://127.0.0.1:8080/.

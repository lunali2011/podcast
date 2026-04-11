# Podcast Project Status

## Current Goal

Build a consumer-facing podcast site where:

- listeners can browse and play published albums
- the creator can upload text or PDF, generate a podcast album draft, then publish it to the homepage

The current focus is to make the online `studio` flow work safely without exposing any Doubao credentials.

## Important Product Decisions

- The site style should stay on the current night-theme direction.
- The homepage is a listener-facing discovery page.
- `studio.html` is the creator flow for creating a new album.
- Every generated album may be:
  - 1 episode if the source is short
  - multiple episodes if the source is longer
- Target duration is capped at about 8 minutes per episode.
- Cancelled tasks should disappear from the queue.
- Generated albums should feel like real product output, not engineering demo output.
- Any token or secret must stay server-side only.

## Current Frontend Files

- Homepage: [`/Users/liyuemei/Desktop/实验agent/podcast/index.html`](/Users/liyuemei/Desktop/实验agent/podcast/index.html)
- Preview page: [`/Users/liyuemei/Desktop/实验agent/podcast/preview.html`](/Users/liyuemei/Desktop/实验agent/podcast/preview.html)
- Create page: [`/Users/liyuemei/Desktop/实验agent/podcast/studio.html`](/Users/liyuemei/Desktop/实验agent/podcast/studio.html)
- Shared API config: [`/Users/liyuemei/Desktop/实验agent/podcast/site-config.js`](/Users/liyuemei/Desktop/实验agent/podcast/site-config.js)
- Published album data: [`/Users/liyuemei/Desktop/实验agent/podcast/albums.json`](/Users/liyuemei/Desktop/实验agent/podcast/albums.json)

## Current Backend File

- API server: [`/Users/liyuemei/Desktop/实验agent/podcast/podcast_test_server.py`](/Users/liyuemei/Desktop/实验agent/podcast/podcast_test_server.py)

## What Is Already Working

### Public Website

- GitHub repo: [https://github.com/lunali2011/podcast](https://github.com/lunali2011/podcast)
- Public site: [https://lunali2011.github.io/podcast/](https://lunali2011.github.io/podcast/)
- The homepage already reads published albums from `albums.json`.

### Local / Creator Flow

- `studio.html` already supports:
  - title
  - intro
  - upload `.txt` and `.pdf`
  - paste text mode
  - background job queue
  - generation progress
  - completion modal
  - listen now / listen later flow
  - publish-to-homepage logic

### Doubao / Podcast API

- The Doubao podcast WebSocket flow has already been validated successfully.
- Default dual-speaker setup was confirmed working earlier in local testing.
- Credentials must never go into the frontend.

### ECS Server

- Cloud server vendor: Volcengine ECS
- Public IP: `115.190.112.80`
- OS: Ubuntu 20.04
- Python virtualenv has been created on the server
- Backend dependencies installed:
  - `websockets`
  - `certifi`
  - system packages already installed earlier:
    - `python3`
    - `python3-pip`
    - `python3-venv`
    - `git`
    - `nginx`
    - `poppler-utils`
- The backend server was successfully started on ECS and confirmed reachable from a local machine via:
  - `http://115.190.112.80:8765/api/albums`

## Very Important Security Rule

Never put the Doubao credentials in:

- frontend code
- `site-config.js`
- GitHub repository
- browser-side requests

They must stay only in the server-side `.env` on ECS.

## Current Deployment Reality

### What works now

- The backend is reachable over **HTTP** on ECS:
  - `http://115.190.112.80:8765`

### What does NOT work yet for public GitHub Pages

- GitHub Pages is served over `HTTPS`
- the current backend is only `HTTP`
- so the public `studio.html` cannot safely call that API yet because of mixed-content browser blocking

## Why The Domain Matters

To make the public online `studio` page work, the backend needs:

- a domain, planned: `api.zhoubeihang.com`
- HTTPS
- likely `nginx` reverse proxy to the Python server on port `8765`

Only after that should the public frontend point `site-config.js` at the remote API.

## Current API Direction

The frontend was already prepared to support a remote API base.

Current change:

- `index.html` now tries `GET /api/albums` first, then falls back to local `albums.json`
- `studio.html` uses a shared `API_BASE` helper from `site-config.js`
- `podcast_test_server.py` now exposes `GET /api/albums`

## Server Notes

### Current listening host

The backend server was changed from:

- `127.0.0.1`

to:

- `0.0.0.0`

so ECS can accept public requests on port `8765`.

### Security group

Port `8765/TCP` was opened temporarily for testing.

Longer-term preferred setup:

- open `80`
- open `443`
- put `nginx` in front
- keep raw Python service internal if desired

## Data / Storage Status

### Right now

- published albums are stored in `albums.json`
- published audio is under [`/Users/liyuemei/Desktop/实验agent/podcast/mp3`](/Users/liyuemei/Desktop/实验agent/podcast/mp3)
- generated server-side audio can be created on ECS local disk

### Recommended longer-term direction

- compute: Volcengine ECS
- audio storage: Volcengine TOS
- metadata: JSON first, then database if needed later

## Current Git Working Tree

At the time this file was written, local repo changes were not fully committed yet.

Changed files:

- [`/Users/liyuemei/Desktop/实验agent/podcast/index.html`](/Users/liyuemei/Desktop/实验agent/podcast/index.html)
- [`/Users/liyuemei/Desktop/实验agent/podcast/studio.html`](/Users/liyuemei/Desktop/实验agent/podcast/studio.html)
- [`/Users/liyuemei/Desktop/实验agent/podcast/podcast_test_server.py`](/Users/liyuemei/Desktop/实验agent/podcast/podcast_test_server.py)
- new file: [`/Users/liyuemei/Desktop/实验agent/podcast/site-config.js`](/Users/liyuemei/Desktop/实验agent/podcast/site-config.js)

## Next Recommended Steps

### Path A: continue public online studio

1. Add DNS record for `api.zhoubeihang.com` -> `115.190.112.80`
2. Install and configure `nginx`
3. Get HTTPS working
4. Point `site-config.js` to `https://api.zhoubeihang.com`
5. Re-test homepage and `studio.html`

### Path B: pause public studio and keep creator flow private for now

1. Keep the public site as a listener-facing site only
2. Use creator flow locally or via direct server access
3. Resume HTTPS/domain work later

## Exact Resume Point For Next Session

If resuming later, start from this question:

> Has `api.zhoubeihang.com` already been pointed to `115.190.112.80`, and do we want to continue the HTTPS setup now?

If yes, next work should be:

1. verify DNS resolution
2. configure `nginx`
3. set up HTTPS
4. update `site-config.js`
5. test public `studio.html`


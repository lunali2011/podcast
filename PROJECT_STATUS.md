# Podcast Project Status

## Current Goal

Build a consumer-facing podcast site where:

- listeners can browse and play published albums
- the creator can upload text or PDF, generate a podcast album draft, then publish it to the homepage

The current focus is to keep the whole site running on Volcengine under one domain and continue improving the creator flow without exposing any Doubao credentials.

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
- Main production site is now intended to run from Volcengine:
  - [https://lunapodcast.top](https://lunapodcast.top)
- Legacy GitHub Pages URL still exists as a fallback / old entry:
  - [https://lunali2011.github.io/podcast/](https://lunali2011.github.io/podcast/)
- The homepage uses `albums.json` as the current album source.

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
- The backend is now managed by `systemd`
- Service name:
  - `luna-podcast-api`
- The backend has already been verified locally on the server through:
  - `https://api.lunapodcast.top/api/albums`

## Very Important Security Rule

Never put the Doubao credentials in:

- frontend code
- `site-config.js`
- GitHub repository
- browser-side requests

They must stay only in the server-side `.env` on ECS.

## Current Deployment Reality

### Current intended production architecture

- Frontend static files are intended to be served from Volcengine ECS
- Backend API is served from the same ECS machine
- Nginx fronts the site and proxies `/api/` to Python on `127.0.0.1:8765`
- Main domain:
  - `https://lunapodcast.top`
- API domain also exists:
  - `https://api.lunapodcast.top`

### Important note

- During migration, GitHub Pages may still show older or fallback behavior
- The long-term target is to stop depending on GitHub Pages for production traffic

## Current API Direction

- Same-origin is now the preferred direction
- [`/Users/liyuemei/Desktop/实验agent/podcast/site-config.js`](/Users/liyuemei/Desktop/实验agent/podcast/site-config.js) is now intended to stay:
  - `window.LUNA_API_BASE = "";`
- That means:
  - frontend pages should call `/api/...`
  - Nginx handles routing

## Server Notes

### Current listening host

The backend server was changed from:

- `127.0.0.1`

to:

- `0.0.0.0`

so ECS can accept requests that Nginx proxies internally.

### Security group

Current important inbound ports:

- `22/TCP`
- `80/TCP`
- `443/TCP`
- `8765/TCP` was opened during debugging and can be removed later if only Nginx is used publicly

## Data / Storage Status

### Right now

- published albums are stored in `albums.json`
- published audio is under [`/Users/liyuemei/Desktop/实验agent/podcast/mp3`](/Users/liyuemei/Desktop/实验agent/podcast/mp3)
- generated server-side audio can be created on ECS local disk

### Recommended longer-term direction

- compute: Volcengine ECS
- audio storage: Volcengine TOS
- metadata: JSON first, then database if needed later

## Current Deployment Files

- Main migration note:
  - [`/Users/liyuemei/Desktop/实验agent/podcast/deploy/VOLCENGINE_MIGRATION.md`](/Users/liyuemei/Desktop/实验agent/podcast/deploy/VOLCENGINE_MIGRATION.md)
- Nginx template:
  - [`/Users/liyuemei/Desktop/实验agent/podcast/deploy/lunapodcast.nginx.conf`](/Users/liyuemei/Desktop/实验agent/podcast/deploy/lunapodcast.nginx.conf)

## Next Recommended Steps

1. Validate production pages on:
   - `https://lunapodcast.top`
   - `https://lunapodcast.top/studio.html`
2. Confirm homepage album data and audio paths all work correctly when served from ECS
3. Confirm `studio` upload / job / publish flow works on the production domain
4. If stable, gradually treat GitHub Pages as backup instead of primary
5. Later:
   - decide whether to move generated audio to Volcengine TOS
   - optionally close public `8765` if no longer needed

## Exact Resume Point For Next Session

If resuming later, start from this question:

> Is `https://lunapodcast.top` already serving both frontend pages and `/api/*` correctly for real browser use?

If not, next work should be:

1. verify current Nginx live config on ECS
2. verify frontend static root on ECS
3. test `/api/albums`, homepage, and `studio.html`
4. fix any remaining production-domain issues before returning to feature work

# Instagram Carousel Generator

**Turn a single idea into a fully designed, ready-to-post Instagram carousel — in seconds.**

No writing. No design. No manual editing. Just type a topic and get back slides, captions, and hashtags.

---

## Overview

This tool automates the full Instagram carousel creation pipeline using AI. You provide a short idea (e.g. `"why beginners fail with Claude"`), and the system returns:

- **4–7 fully rendered slide images** (1080×1350px PNG, retina-quality)
- **A ready-to-post caption** with hook, value lines, CTA, and hashtags

Every part of the output is rules-enforced — word limits, content structure, emphasis style, and caption format — so the slides are always clean and ready to publish without editing.

---

## How It Works

```
User types a topic
       │
       ▼
Claude generates structured slide content (JSON)
  • Hook → Value slides → CTA
  • Word limits: hook ≤8w, content ≤15w, CTA ≤12w
  • At least one actionable "Instead of X → Try Y" tip
  • Selective bold on 1–2 key words per slide
       │
       ▼
Content injected into HTML templates
       │
       ▼
Playwright renders each slide → high-res PNG
       │
       ▼
Caption generated (hook + insight + CTA + hashtags)
       │
       ▼
Frontend displays slides + caption, ready to save or copy
```

Results stream back to the UI in real time via Server-Sent Events.

---

## Features

- **Single-input generation** — type a topic, the system handles structure and quality
- **4–7 dynamic slides** — slide count adapts to the content, not a fixed template
- **Enforced word limits** — clean layout is guaranteed by post-generation code, not LLM promises
- **Content rules** — every carousel must include a complete hook, at least one actionable tip, and a clear CTA
- **Selective emphasis** — 1–2 key words bolded per slide using `**markdown**` converted to HTML `<strong>`
- **High-res PNG output** — slides rendered at 2× device pixel ratio (2160×2700px effective)
- **Instagram caption** — structured caption with body, CTA line, and hashtags separated by correct spacing
- **Mobile-first saving** — uses Web Share API on iOS/Android to save directly to the camera roll; falls back to `<a download>` on desktop
- **Download All** — desktop users can download all slides in one click
- **Fullscreen preview** — tap any slide to view it fullscreen with prev/next navigation
- **SSE streaming** — progress updates stream to the frontend as each pipeline stage completes
- **Dual LLM support** — Anthropic Claude (default) or OpenAI, switchable via env var

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python, FastAPI |
| LLM | Anthropic Claude (`claude-sonnet-4-6`) |
| Slide rendering | Playwright (Chromium headless) |
| Frontend | React, Vite, Tailwind CSS |
| Deployment | Docker, Railway |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        Browser                          │
│   React + Tailwind  ──SSE──►  /generate (POST)          │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│                      FastAPI (app.py)                   │
│                                                         │
│   generate_slides(topic)          generate_caption()    │
│          │                               │              │
│          ▼                               ▼              │
│    generator.py                   generator.py          │
│    (Anthropic/OpenAI)             (Anthropic/OpenAI)    │
│          │                                              │
│          ▼                                              │
│    renderer.py                                          │
│    inject_slide() → HTML                                │
│    Playwright → PNG                                     │
│          │                                              │
│          ▼                                              │
│    /tmp/renders/<run_id>/slide-n.png                    │
│    served via StaticFiles at /renders/                  │
└─────────────────────────────────────────────────────────┘
```

**Key files:**

```
├── app.py              # FastAPI app, SSE streaming, route handlers
├── generator.py        # LLM slide + caption generation, all validation logic
├── renderer.py         # HTML template injection, Playwright PNG rendering
├── main.py             # Entry point (server mode via PORT env var)
├── slide-first.html    # Hook slide template
├── slide-content.html  # Content slide template ({{TEXT}}, {{NUMBER}})
├── slide-last.html     # CTA slide template
└── frontend/
    └── src/
        └── components/
            ├── Output.jsx   # Gallery, lightbox, caption card, save logic
            └── Form.jsx     # Topic input form
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- An Anthropic API key (or OpenAI key)

### 1. Clone the repo

```bash
git clone https://github.com/dominicjtaylor/media-generator.git
cd media-generator
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright browsers

```bash
playwright install chromium
```

### 4. Set environment variables

```bash
cp .env.example .env
# Edit .env and add your API key
```

### 5. Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

### 6. Run the server

```bash
python main.py
```

Open [http://localhost:8000](http://localhost:8000).

---

## Usage

### Via the web UI

1. Open the app in your browser
2. Type a topic — for example:

   > `why most people fail with Claude`

3. Click **Generate**
4. Slides render in real time as the pipeline runs
5. On mobile: tap **Save to Photos** under each slide
6. On desktop: click **Download All** or download individually
7. Copy the generated caption and paste it into Instagram

### Via the API directly

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"topic": "why most people fail with Claude"}' \
  --no-buffer
```

The response is an SSE stream. Events:

```
data: {"step": "generating", "message": "Generating carousel content..."}
data: {"step": "rendering",  "message": "Rendering slides..."}
data: {"step": "complete",   "images": ["/renders/<id>/slide-1.png", ...], "slides": [...], "caption": "..."}
```

### Example output

**Input:** `"why most people fail with Claude"`

**Slides generated:**
```
[HOOK]    You're using Claude — most people never **unlock** it
[CONTENT] Instead of long prompts → give one **specific** instruction
[CONTENT] **Context** is everything — Claude forgets between sessions
[CONTENT] Vague questions = vague answers. Be **precise**
[CTA]     **Save** this and fix one prompt today
```

**Caption:**
```
You're using Claude wrong — and it's costing you results.

Most people treat it like a search engine.
That's why they get generic, useless answers.

One specific prompt beats ten vague ones.
Give Claude context and your output transforms.

Follow @claudeinsights for more AI tips

#ClaudeAI #AItools #Productivity #AITips
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (if using Claude) | Anthropic API key |
| `OPENAI_API_KEY` | Yes (if using OpenAI) | OpenAI API key |
| `LLM_PROVIDER` | No | `anthropic` (default) or `openai` |
| `OPENAI_MODEL` | No | OpenAI model name (default: `gpt-4o`) |
| `PORT` | No | Server port (default: `8000`) |
| `REVIEW_ENABLED` | No | Set to `false` to skip the review pass and save one LLM call (default: `true`) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

Copy `.env.example` to `.env` and fill in the values before running locally.

---

## Deployment

### Docker (local)

```bash
docker build -t carousel-generator .
docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY=your_key_here \
  carousel-generator
```

### Railway

The project is configured to deploy on [Railway](https://railway.app) using the included `Dockerfile`.

1. Push the repo to GitHub
2. Create a new Railway project → **Deploy from GitHub repo**
3. Add environment variables in the Railway dashboard (see table above)
4. Railway builds the Docker image and deploys automatically on each push

**Notes:**
- The Dockerfile uses `mcr.microsoft.com/playwright/python:v1.58.0-jammy` — Chromium is pre-installed, no browser download needed at runtime
- Rendered PNGs are written to `/tmp/renders/` (ephemeral — expected for a stateless deployment)
- The `PORT` environment variable is set automatically by Railway and read by `main.py`

---

## Roadmap

- [ ] Brand customisation (colours, fonts, logo via UI)
- [ ] Multiple visual templates (light mode, minimal, bold)
- [ ] Persistent storage for generated carousels (S3 / R2)
- [ ] Scheduling — generate and queue carousels for specific dates
- [ ] Direct Instagram publishing via Instagram Graph API
- [ ] Topic history and regeneration from the UI
- [ ] OpenAI image generation for cover slides
- [ ] Multi-language support

---

## Contributing

Contributions are welcome. To get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes with clear, focused commits
4. Open a pull request with a description of what changed and why

For significant changes, open an issue first to discuss the approach.

Please keep PRs focused — one feature or fix per PR.

---

## License

MIT License. See `LICENSE` for details.

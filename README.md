# AuCl — Auto-Clipper Engine (Phase 1)

Local, single-machine pipeline that turns trend **metadata** into review-ready Instagram Reel drafts (MP4 + caption + hashtags). **No auto-posting** in Phase 1 — you review locally and post by hand.

## Layout

```
AuCl/
├── orchestrator/          # CLI DAG runner
├── modules/
│   ├── common/            # shared Pydantic contracts, settings, SQLite
│   ├── trend_signal/
│   ├── concept_planner/
│   ├── asset_generator/
│   ├── assembly_engine/
│   └── formatter/
├── dashboard/             # FastAPI review UI
├── data/                  # signals, concepts, assets, fixtures, SQLite
├── output/                # finished clips for review
└── config/settings.yaml
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Recommended: one key only (Anthropic)

Put your Claude key in `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

Then run:

```bash
python -m orchestrator.run_pipeline
```

Claude uses Anthropic **web search** to research comedy/meme trends (no YouTube/Reddit keys). Stock footage and voice fall back to local ffmpeg/TTS when those keys are empty. Concepts + captions also use Claude.

### Fully key-free demo

```bash
python -m orchestrator.run_pipeline --smoke
```

### Review UI

```bash
uvicorn dashboard.app:app --reload --port 8000
```

Open http://127.0.0.1:8000 — approve/reject clips, then post manually.

## Cron (optional daily on-demand)

```cron
0 9 * * * cd /path/to/AuCl && .venv/bin/python -m orchestrator.run_pipeline >> logs/aucl.log 2>&1
```

## Legal lanes

| Lane | Sources | Notes |
|------|---------|-------|
| Signal (metadata only) | YouTube Data API, Reddit, Google Trends | No video download |
| Asset (pixels in output) | AI TTS/video, Pexels/Pixabay commercial licenses | Original premises via LLM |

## Stubbed in Phase 1

- `ai_video` strategy — interface ready; configure Runway/Pika/Luma later
- Instagram Graph API auto-publish — Phase 3

## Tests

```bash
pytest -q
```

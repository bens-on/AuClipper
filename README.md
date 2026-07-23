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
cp .env.example .env   # optional for live APIs; not needed for smoke

# No-op stage walkthrough
python -m orchestrator.run_pipeline --dry-run

# Key-free end-to-end (fixtures + local TTS + ffmpeg)
python -m orchestrator.run_pipeline --smoke

# Review UI
uvicorn dashboard.app:app --reload --port 8000
```

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

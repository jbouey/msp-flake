# OsirisCare Demo Video Pipeline

Automated demo video generation using AI voice + avatar overlay on screen recordings.

## Stack

| Component | Tool | Why |
|-----------|------|-----|
| Voice | **ElevenLabs** | #1 realism, emotional nuance, voice cloning. Reddit/creator consensus. |
| Avatar | **HeyGen Avatar IV** | Most realistic lip-sync + micro-expressions. Circle overlay native. |
| Compositing | **FFmpeg** | Overlay avatar bubble on screen recordings |
| Screen Recording | **You** | Mouse movements, clicking through the real product |

## Setup

```bash
# Install dependencies
pip install elevenlabs httpx python-dotenv

# Configure API keys
cp .env.example .env
# Edit .env with your ElevenLabs + HeyGen API keys

# Generate a demo video
python generate.py scripts/01-dashboard-tour.md
```

## Workflow

1. **Write script** in `scripts/` (markdown with timing cues)
2. **Record screen** — just mouse movements through the real product
3. **Run `generate.py`** — sends script to ElevenLabs + HeyGen, gets avatar clip
4. **Run `compose.sh`** — FFmpeg overlays avatar circle on your recording
5. **Add graphics** — title cards, callouts, transitions in your editor

## Directory Structure

```
scripts/          # Markdown demo scripts with timing cues
recordings/       # Your raw screen recordings (.mov/.mp4)
avatars/          # Generated avatar clips from HeyGen
assets/           # Logos, title cards, lower thirds
output/           # Final composited videos
generate.py       # ElevenLabs audio + HeyGen avatar generation
compose.sh        # FFmpeg overlay composition
```

## Demo Catalog

| # | Demo | Duration | Script |
|---|------|----------|--------|
| 01 | Dashboard Tour | 90s | `scripts/01-dashboard-tour.md` |
| 02 | Incident Auto-Healing | 2min | `scripts/02-incident-healing.md` |
| 03 | Compliance Evidence | 90s | `scripts/03-compliance-packet.md` |
| 04 | Fleet Deployment | 60s | `scripts/04-fleet-deployment.md` |
| 05 | Drift Detection Live | 90s | `scripts/05-drift-detection.md` |
| 06 | Client Portal | 60s | `scripts/06-client-portal.md` |

## Avatar Style

- **Format**: Circle bubble, bottom-right corner (picture-in-picture)
- **Size**: 200px diameter on 1920x1080 canvas
- **Position**: 48px from bottom-right edges
- **Background**: Transparent (green screen from HeyGen, keyed in FFmpeg)
- **Persona**: Professional, warm, knowledgeable — "your compliance expert"

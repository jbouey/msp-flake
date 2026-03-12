#!/usr/bin/env python3
"""
OsirisCare Demo Video Generator

Generates AI voice narration (ElevenLabs) and talking-head avatar clips (HeyGen)
from markdown scripts. Output is composited with screen recordings via compose.sh.

Usage:
    python generate.py scripts/01-dashboard-tour.md
    python generate.py scripts/01-dashboard-tour.md --voice-only
    python generate.py scripts/01-dashboard-tour.md --avatar-only
    python generate.py --list-voices
    python generate.py --list-avatars
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

# --- Config ---

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"

HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY", "")
HEYGEN_AVATAR_ID = os.getenv("HEYGEN_AVATAR_ID", "")
HEYGEN_BASE = "https://api.heygen.com/v2"

AVATAR_SIZE = int(os.getenv("AVATAR_SIZE", "200"))
OUTPUT_DIR = Path("avatars")
AUDIO_DIR = Path("avatars")  # audio files go here too


# --- Script Parser ---

def parse_script(script_path: Path) -> dict:
    """Parse a markdown demo script into structured segments.

    Expected format:
    ---
    title: Dashboard Tour
    duration: 90s
    demo_file: 01-dashboard-tour
    ---

    ## Scene 1: Opening
    [SCREEN: Navigate to dashboard]

    Welcome to OsirisCare. This is your compliance command center...

    ## Scene 2: Compliance Score
    [SCREEN: Click on compliance score widget]

    Notice the compliance health score here...
    """
    text = script_path.read_text()

    # Extract frontmatter
    meta = {}
    fm_match = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).strip().split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                meta[key.strip()] = val.strip()
        text = text[fm_match.end():].strip()

    # Extract narration (strip scene headers and stage directions)
    narration_lines = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("##"):
            continue  # scene headers
        if line.startswith("[") and line.endswith("]"):
            continue  # stage directions
        if line:
            narration_lines.append(line)

    narration = " ".join(narration_lines)

    # Extract scenes
    scenes = []
    scene_blocks = re.split(r"^## ", text, flags=re.MULTILINE)
    for block in scene_blocks:
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        title = lines[0].strip()
        directions = []
        speech = []
        for line in lines[1:]:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                directions.append(line[1:-1])
            elif line:
                speech.append(line)
        scenes.append({
            "title": title,
            "directions": directions,
            "speech": " ".join(speech),
        })

    return {
        "meta": meta,
        "narration": narration,
        "scenes": scenes,
        "raw": text,
    }


# --- ElevenLabs Voice Generation ---

def list_voices():
    """List available ElevenLabs voices."""
    resp = httpx.get(
        f"{ELEVENLABS_BASE}/voices",
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    voices = resp.json()["voices"]
    print(f"\n{'Name':<30} {'ID':<25} {'Category':<15}")
    print("-" * 70)
    for v in voices:
        print(f"{v['name']:<30} {v['voice_id']:<25} {v.get('category', 'n/a'):<15}")
    return voices


def generate_voice(text: str, output_path: Path, voice_id: str = "") -> Path:
    """Generate speech audio from text using ElevenLabs."""
    vid = voice_id or ELEVENLABS_VOICE_ID
    if not vid:
        print("ERROR: No voice ID. Run --list-voices to pick one, set ELEVENLABS_VOICE_ID in .env")
        sys.exit(1)

    print(f"  Generating voice ({len(text)} chars)...")

    resp = httpx.post(
        f"{ELEVENLABS_BASE}/text-to-speech/{vid}",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.6,        # Slightly varied = more natural
                "similarity_boost": 0.85,  # Stay close to voice character
                "style": 0.4,             # Some expressiveness
                "use_speaker_boost": True,
            },
        },
        timeout=120,
    )
    resp.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(resp.content)
    size_kb = len(resp.content) / 1024
    print(f"  Audio saved: {output_path} ({size_kb:.0f} KB)")
    return output_path


# --- HeyGen Avatar Generation ---

def list_avatars():
    """List available HeyGen avatars."""
    resp = httpx.get(
        f"{HEYGEN_BASE}/avatars",
        headers={"X-Api-Key": HEYGEN_API_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    avatars = resp.json().get("data", {}).get("avatars", [])
    print(f"\n{'Name':<35} {'ID':<40} {'Type':<10}")
    print("-" * 85)
    for a in avatars:
        print(f"{a.get('avatar_name', 'unnamed'):<35} {a['avatar_id']:<40} {a.get('type', 'n/a'):<10}")
    return avatars


def generate_avatar_video(
    text: str,
    audio_path: Path,
    output_path: Path,
    avatar_id: str = "",
) -> Path:
    """Generate a talking-head avatar video using HeyGen.

    Uses the audio from ElevenLabs as the voice track, with HeyGen
    providing the lip-synced avatar animation.
    """
    aid = avatar_id or HEYGEN_AVATAR_ID
    if not aid:
        print("ERROR: No avatar ID. Run --list-avatars to pick one, set HEYGEN_AVATAR_ID in .env")
        sys.exit(1)

    print(f"  Uploading audio to HeyGen...")

    # Step 1: Upload audio file to HeyGen
    upload_resp = httpx.post(
        f"{HEYGEN_BASE}/assets",
        headers={"X-Api-Key": HEYGEN_API_KEY},
        files={"file": (audio_path.name, audio_path.read_bytes(), "audio/mpeg")},
        timeout=60,
    )
    upload_resp.raise_for_status()
    audio_asset_id = upload_resp.json()["data"]["id"]
    print(f"  Audio uploaded: {audio_asset_id}")

    # Step 2: Create avatar video with uploaded audio
    print(f"  Creating avatar video (avatar: {aid})...")
    video_resp = httpx.post(
        f"{HEYGEN_BASE}/video/generate",
        headers={
            "X-Api-Key": HEYGEN_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": aid,
                        "avatar_style": "normal",
                    },
                    "voice": {
                        "type": "audio",
                        "audio_asset_id": audio_asset_id,
                    },
                    "background": {
                        "type": "color",
                        "value": "#00FF00",  # Green screen for chroma key
                    },
                }
            ],
            "dimension": {
                "width": AVATAR_SIZE * 2,   # Render at 2x for quality
                "height": AVATAR_SIZE * 2,
            },
            "aspect_ratio": "1:1",  # Square for circle crop
        },
        timeout=60,
    )
    video_resp.raise_for_status()
    video_id = video_resp.json()["data"]["video_id"]
    print(f"  Video queued: {video_id}")

    # Step 3: Poll for completion
    print(f"  Waiting for render", end="", flush=True)
    for _ in range(120):  # Up to 10 minutes
        time.sleep(5)
        print(".", end="", flush=True)

        status_resp = httpx.get(
            f"{HEYGEN_BASE}/video_status.get",
            headers={"X-Api-Key": HEYGEN_API_KEY},
            params={"video_id": video_id},
            timeout=30,
        )
        status_resp.raise_for_status()
        data = status_resp.json()["data"]

        if data["status"] == "completed":
            video_url = data["video_url"]
            print(f"\n  Downloading avatar video...")

            dl_resp = httpx.get(video_url, timeout=120)
            dl_resp.raise_for_status()

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl_resp.content)
            size_mb = len(dl_resp.content) / (1024 * 1024)
            print(f"  Avatar video saved: {output_path} ({size_mb:.1f} MB)")
            return output_path

        if data["status"] == "failed":
            print(f"\n  ERROR: Video generation failed: {data.get('error', 'unknown')}")
            sys.exit(1)

    print("\n  ERROR: Timed out waiting for video render")
    sys.exit(1)


# --- Main ---

def process_script(script_path: str, voice_only: bool = False, avatar_only: bool = False):
    """Process a demo script end-to-end."""
    path = Path(script_path)
    if not path.exists():
        print(f"ERROR: Script not found: {path}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Processing: {path.name}")
    print(f"{'='*60}")

    parsed = parse_script(path)
    meta = parsed["meta"]
    demo_file = meta.get("demo_file", path.stem)

    print(f"  Title: {meta.get('title', 'Untitled')}")
    print(f"  Duration: {meta.get('duration', 'unknown')}")
    print(f"  Scenes: {len(parsed['scenes'])}")
    print(f"  Narration: {len(parsed['narration'])} chars")
    print()

    audio_path = AUDIO_DIR / f"{demo_file}.mp3"
    avatar_path = OUTPUT_DIR / f"{demo_file}-avatar.mp4"

    # Generate voice
    if not avatar_only:
        if not ELEVENLABS_API_KEY:
            print("  SKIP: No ELEVENLABS_API_KEY set")
        else:
            generate_voice(parsed["narration"], audio_path)

    # Generate avatar
    if not voice_only:
        if not HEYGEN_API_KEY:
            print("  SKIP: No HEYGEN_API_KEY set")
        elif not audio_path.exists():
            print("  SKIP: No audio file yet. Run --voice-only first.")
        else:
            generate_avatar_video(parsed["narration"], audio_path, avatar_path)

    print(f"\n  Done! Next steps:")
    print(f"    1. Record screen: save to recordings/{demo_file}.mov")
    print(f"    2. Compose: ./compose.sh recordings/{demo_file}.mov {avatar_path}")
    print()


def main():
    parser = argparse.ArgumentParser(description="OsirisCare Demo Video Generator")
    parser.add_argument("script", nargs="?", help="Path to demo script (.md)")
    parser.add_argument("--voice-only", action="store_true", help="Only generate voice audio")
    parser.add_argument("--avatar-only", action="store_true", help="Only generate avatar video")
    parser.add_argument("--list-voices", action="store_true", help="List ElevenLabs voices")
    parser.add_argument("--list-avatars", action="store_true", help="List HeyGen avatars")
    args = parser.parse_args()

    if args.list_voices:
        if not ELEVENLABS_API_KEY:
            print("ERROR: Set ELEVENLABS_API_KEY in .env")
            sys.exit(1)
        list_voices()
        return

    if args.list_avatars:
        if not HEYGEN_API_KEY:
            print("ERROR: Set HEYGEN_API_KEY in .env")
            sys.exit(1)
        list_avatars()
        return

    if not args.script:
        parser.print_help()
        sys.exit(1)

    process_script(args.script, args.voice_only, args.avatar_only)


if __name__ == "__main__":
    main()

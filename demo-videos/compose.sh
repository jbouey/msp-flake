#!/usr/bin/env bash
#
# Compose a demo video: overlay avatar circle bubble on screen recording
#
# Usage:
#   ./compose.sh recordings/01-dashboard-tour.mov avatars/01-dashboard-tour-avatar.mp4
#   ./compose.sh recordings/01-dashboard-tour.mov avatars/01-dashboard-tour-avatar.mp4 --position top-right
#   ./compose.sh recordings/01-dashboard-tour.mov avatars/01-dashboard-tour-avatar.mp4 --size 250
#

set -euo pipefail

SCREEN_RECORDING="${1:?Usage: ./compose.sh <screen-recording> <avatar-video> [--position pos] [--size px]}"
AVATAR_VIDEO="${2:?Usage: ./compose.sh <screen-recording> <avatar-video> [--position pos] [--size px]}"

# Defaults
POSITION="bottom-right"
SIZE=200
MARGIN=48

# Parse optional args
shift 2
while [[ $# -gt 0 ]]; do
    case "$1" in
        --position) POSITION="$2"; shift 2 ;;
        --size)     SIZE="$2"; shift 2 ;;
        --margin)   MARGIN="$2"; shift 2 ;;
        *)          echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Derive output filename
BASENAME=$(basename "$SCREEN_RECORDING" | sed 's/\.[^.]*$//')
OUTPUT="output/${BASENAME}-final.mp4"

echo "=========================================="
echo "  OsirisCare Demo Compositor"
echo "=========================================="
echo "  Screen:   $SCREEN_RECORDING"
echo "  Avatar:   $AVATAR_VIDEO"
echo "  Position: $POSITION"
echo "  Size:     ${SIZE}px circle"
echo "  Margin:   ${MARGIN}px"
echo "  Output:   $OUTPUT"
echo ""

# Calculate overlay position
# main_w and main_h refer to the screen recording dimensions
case "$POSITION" in
    bottom-right) OVERLAY_X="main_w-overlay_w-${MARGIN}"; OVERLAY_Y="main_h-overlay_h-${MARGIN}" ;;
    bottom-left)  OVERLAY_X="${MARGIN}";                   OVERLAY_Y="main_h-overlay_h-${MARGIN}" ;;
    top-right)    OVERLAY_X="main_w-overlay_w-${MARGIN}"; OVERLAY_Y="${MARGIN}" ;;
    top-left)     OVERLAY_X="${MARGIN}";                   OVERLAY_Y="${MARGIN}" ;;
    *)            echo "Invalid position. Use: bottom-right, bottom-left, top-right, top-left"; exit 1 ;;
esac

mkdir -p output

echo "  Compositing..."

# FFmpeg pipeline:
# 1. Scale avatar to target size
# 2. Chroma key the green background
# 3. Crop to circle using alpha mask
# 4. Add subtle drop shadow
# 5. Overlay on screen recording
# 6. Mix audio from both sources (avatar voice + optional screen audio)

ffmpeg -y \
    -i "$SCREEN_RECORDING" \
    -i "$AVATAR_VIDEO" \
    -filter_complex "
        [1:v]scale=${SIZE}:${SIZE}[scaled];
        [scaled]chromakey=0x00FF00:0.15:0.1[keyed];
        [keyed]format=rgba,
            geq=
                r='r(X,Y)':
                g='g(X,Y)':
                b='b(X,Y)':
                a='if(lte(pow(X-${SIZE}/2,2)+pow(Y-${SIZE}/2,2),pow(${SIZE}/2-2,2)),a(X,Y),0)'
        [circle];
        color=black@0.3:${SIZE}x${SIZE},
            format=rgba,
            geq=
                r='r(X,Y)':
                g='g(X,Y)':
                b='b(X,Y)':
                a='if(lte(pow(X-${SIZE}/2,2)+pow(Y-${SIZE}/2,2),pow(${SIZE}/2,2)),255*0.3,0)'
        [shadow];
        [0:v][shadow]overlay=x=${OVERLAY_X}+3:y=${OVERLAY_Y}+3:shortest=1[with_shadow];
        [with_shadow][circle]overlay=x=${OVERLAY_X}:y=${OVERLAY_Y}:shortest=1[vout];
        [1:a]volume=1.0[avatar_audio];
        [0:a]volume=0.1[screen_audio];
        [avatar_audio][screen_audio]amix=inputs=2:duration=shortest[aout]
    " \
    -map "[vout]" -map "[aout]" \
    -c:v libx264 -preset medium -crf 20 \
    -c:a aac -b:a 192k \
    -movflags +faststart \
    "$OUTPUT"

echo ""
echo "  Done! Output: $OUTPUT"
echo "  Size: $(du -h "$OUTPUT" | cut -f1)"
echo ""

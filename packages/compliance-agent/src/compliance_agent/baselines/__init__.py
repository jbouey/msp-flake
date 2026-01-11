"""
Baseline configurations for drift detection.

Contains YAML baseline definitions for:
- Linux servers (linux_baseline.yaml)
- Network posture (network_posture.yaml)
"""

from pathlib import Path

BASELINES_DIR = Path(__file__).parent

LINUX_BASELINE = BASELINES_DIR / "linux_baseline.yaml"
NETWORK_POSTURE_BASELINE = BASELINES_DIR / "network_posture.yaml"

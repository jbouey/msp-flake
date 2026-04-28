#!/usr/bin/env python3
"""
Context Manager - JSON-based context management for AI sessions.

Based on Anthropic's research:
- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- https://01.me/en/2025/12/context-engineering-from-claude/

Commands:
  status      - Show current state from claude-progress.json
  new-session - Start a new session
  end-session - Complete current session
  update      - Update a field in progress file
  compact     - Archive old sessions
  validate    - Check consistency
  migrate     - Migrate old markdown files to new structure
"""

import os
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import re
import sys

AGENT_DIR = Path(__file__).parent.parent
PROJECT_ROOT = AGENT_DIR.parent
SESSIONS_DIR = AGENT_DIR / "sessions"
ARCHIVE_DIR = AGENT_DIR / "archive"
REFERENCE_DIR = AGENT_DIR / "reference"
PROGRESS_FILE = AGENT_DIR / "claude-progress.json"

# Files to migrate/deprecate
DEPRECATED_FILES = [
    "CONTEXT.md",
    "TODO.md",
    "CURRENT_STATE.md",
    "SESSION_HANDOFF.md",
    "SESSION_COMPLETION_STATUS.md",
]


def load_progress() -> dict:
    """Load progress from JSON file."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {}


def save_progress(data: dict):
    """Save progress to JSON file."""
    data["updated"] = datetime.utcnow().isoformat() + "Z"
    with open(PROGRESS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def status():
    """Show current state. Schema v2 aware (Session 205+)."""
    # Auto-compact stale sessions on every status check
    compact_sessions(days_to_keep=14, quiet=True)

    progress = load_progress()
    schema = progress.get("schema_version", "1.0")

    print("=" * 60)
    versions = progress.get("versions", {})
    if isinstance(versions, dict) and versions:
        agent_v = versions.get("agent", "?")
        iso_v = versions.get("iso", "?")
    else:
        agent_v = progress.get("agent_version", "?")
        iso_v = progress.get("iso_version", "?")
    print(f"SESSION {progress.get('session', '?')} | Agent v{agent_v} | ISO {iso_v} | schema v{schema}")
    print(f"Updated: {progress.get('updated', 'never')}")
    print("=" * 60)

    # System health (v2 = list of dicts; v1 = dict)
    print("\nSYSTEM HEALTH:")
    health = progress.get("system_health", [])
    if isinstance(health, list):
        for entry in health:
            status = entry.get("status", "?")
            icon = "✓" if status == "up" else "✗" if status in ("down", "offline", "blocked") else "?"
            note = f"  ({entry.get('notes', '')})" if entry.get("notes") else ""
            print(f"  {icon} {entry.get('component', '?')}: {status}{note}")
    elif isinstance(health, dict):
        for system, status in health.items():
            icon = "✓" if status == "healthy" else "✗" if status in ["offline", "blocked"] else "?"
            print(f"  {icon} {system}: {status}")

    # Current blocker (v1 only — v2 doesn't have this field)
    blocker = progress.get("current_blocker")
    if blocker:
        if isinstance(blocker, str):
            print(f"\nBLOCKER: {blocker}")
        else:
            print(f"\nBLOCKER: {blocker.get('id', 'unknown')}")
            print(f"  {blocker.get('description', '')}")
            print(f"  Solution: {blocker.get('solution', 'unknown')}")

    # Active tasks (v2: empty by design; v1: list of dicts with id/task/status)
    tasks = progress.get("active_tasks", [])
    if tasks:
        print("\nACTIVE TASKS (legacy v1 — TaskCreate is the source of truth in v2):")
        for task in tasks:
            if isinstance(task, dict) and "task" in task:
                status_icon = {"blocked": "🔴", "pending": "⬚", "in_progress": "🔵", "done": "✓"}.get(task.get("status"), "?")
                print(f"  {status_icon} [{task.get('id', '?')}] {task['task']}")
            else:
                print(f"  - {task}")

    # Scheduled followups (v2 — added Session 212 P3 closeout). Each
    # entry: {"due": "YYYY-MM-DDTHH:MMZ", "what": str, "doc": str, "owner": str}.
    # Surfaced on `context-manager.py status` so a session pickup
    # actually sees the deferred work. Items past due date are
    # highlighted; cleared by editing this list, NOT by silent decay.
    followups = progress.get("scheduled_followups", [])
    if followups:
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)
        print("\nSCHEDULED FOLLOWUPS:")
        for f in followups:
            due_str = f.get("due", "")
            try:
                due = _dt.fromisoformat(due_str.replace("Z", "+00:00"))
                overdue = now > due
                marker = "⚠️ OVERDUE" if overdue else "📅"
            except (ValueError, TypeError):
                marker = "📅"
                overdue = False
            print(f"  {marker} due {due_str}: {f.get('what', '?')}")
            if f.get("doc"):
                print(f"     doc: {f['doc']}")
            if f.get("owner"):
                print(f"     owner: {f['owner']}")

    # Recent milestones (v2 field)
    for ms in progress.get("recent_milestones", []):
        print(f"\nMILESTONE {ms.get('when', '?')}: {ms.get('what', '?')}")

    # Completed this session (v1 field)
    completed = progress.get("completed_this_session", [])
    if completed:
        print(f"\nCOMPLETED THIS SESSION ({len(completed)}):")
        for item in completed[-5:]:  # Last 5
            print(f"  ✓ {item}")

    print()


def new_session(session_num: int, description: str):
    """Start a new session."""
    # Auto-compact old sessions before starting fresh
    compact_sessions(days_to_keep=14, quiet=True)

    progress = load_progress()
    today = datetime.now().strftime("%Y-%m-%d")

    # Update progress
    progress["session"] = session_num
    progress["completed_this_session"] = []

    # Create session file
    SESSIONS_DIR.mkdir(exist_ok=True)
    session_file = SESSIONS_DIR / f"{today}-session-{session_num}-{description}.md"

    template = f"""# Session {session_num} - {description.replace('-', ' ').title()}

**Date:** {today}
**Started:** {datetime.now().strftime("%H:%M")}
**Previous Session:** {session_num - 1}

---

## Goals

- [ ]

---

## Progress

### Completed


### Blocked


---

## Files Changed

| File | Change |
|------|--------|

---

## Next Session

1.
"""

    with open(session_file, "w") as f:
        f.write(template)

    save_progress(progress)
    print(f"Created session {session_num}: {session_file.name}")
    print(f"Updated claude-progress.json")


def end_session():
    """End current session."""
    progress = load_progress()
    session_num = progress.get("session", 0)
    today = datetime.now().strftime("%Y-%m-%d")

    # Find today's session file
    session_files = list(SESSIONS_DIR.glob(f"{today}-session-{session_num}-*.md"))

    if session_files:
        session_file = session_files[0]
        print(f"Session file: {session_file.name}")

    # Show summary
    print(f"\nSession {session_num} Summary:")
    print(f"  Completed: {len(progress.get('completed_this_session', []))} items")

    completed = progress.get("completed_this_session", [])
    for item in completed:
        print(f"    ✓ {item}")

    save_progress(progress)
    print(f"\nProgress saved. Ready for session {session_num + 1}.")


def update_field(field: str, value: str):
    """Update a field in progress file."""
    progress = load_progress()

    # Handle nested fields like "system_health.vps_api"
    parts = field.split(".")
    target = progress
    for part in parts[:-1]:
        if part not in target:
            target[part] = {}
        target = target[part]

    # Try to parse as JSON for complex values
    try:
        parsed = json.loads(value)
        target[parts[-1]] = parsed
    except json.JSONDecodeError:
        target[parts[-1]] = value

    save_progress(progress)
    print(f"Updated {field} = {value}")


def add_completed(item: str):
    """Add item to completed_this_session."""
    progress = load_progress()
    if "completed_this_session" not in progress:
        progress["completed_this_session"] = []
    progress["completed_this_session"].append(item)
    save_progress(progress)
    print(f"Added: {item}")


def compact_sessions(days_to_keep: int = 14, quiet: bool = False):
    """Archive old sessions."""
    if not SESSIONS_DIR.exists():
        if not quiet:
            print("No sessions directory")
        return

    cutoff = datetime.now() - timedelta(days=days_to_keep)
    sessions = sorted(SESSIONS_DIR.glob("*.md"))

    to_archive = []
    for session in sessions:
        if session.name == "SESSION_TEMPLATE.md":
            continue
        match = re.match(r'(\d{4}-\d{2}-\d{2})', session.name)
        if match:
            session_date = datetime.strptime(match.group(1), "%Y-%m-%d")
            if session_date < cutoff:
                to_archive.append(session)

    if not to_archive:
        if not quiet:
            print(f"No sessions older than {days_to_keep} days")
        return

    # Create archive
    ARCHIVE_DIR.mkdir(exist_ok=True)

    # Group by month
    monthly = {}
    for session in to_archive:
        match = re.match(r'(\d{4}-\d{2})', session.name)
        if match:
            month = match.group(1)
            if month not in monthly:
                monthly[month] = []
            monthly[month].append(session)

    # Create monthly archives
    for month, files in monthly.items():
        archive_file = ARCHIVE_DIR / f"{month}-sessions.md"

        with open(archive_file, "a") as out:
            if archive_file.stat().st_size == 0:
                out.write(f"# Session Archive - {month}\n\n")

            for session_file in sorted(files):
                out.write(f"\n## {session_file.name}\n\n")
                with open(session_file) as f:
                    lines = f.readlines()[:50]
                    out.writelines(lines)
                    if len(lines) == 50:
                        out.write("\n[truncated...]\n")
                out.write("\n---\n")

                session_file.unlink()
                if not quiet:
                    print(f"Archived: {session_file.name}")

    print(f"Archived {len(to_archive)} sessions to {len(monthly)} monthly files")


def validate():
    """Check consistency. Schema v2 aware (Session 205+). Returns True if clean.

    Checks:
      progress.json:
        - schema_version == "2.0"
        - required top-level keys present
        - system_health is a list of {component, status, last_verified, notes}
        - active_tasks is an empty list (TaskCreate owns that now)

      memory hygiene (user memory):
        - MEMORY.md exists and is ≤ 200 lines (truncation cap)
        - each content line in MEMORY.md is ≤ 200 chars (truncation protection)
        - every `*.md` reference in MEMORY.md resolves to a file
        - each topic file begins with YAML frontmatter (--- block at top)
    """
    issues = []

    # --- progress.json schema v2 ---
    progress = load_progress()
    if progress.get("schema_version") != "2.0":
        issues.append(
            f'progress.json schema_version != "2.0" '
            f'(got {progress.get("schema_version")!r}) — see .agent/archive/claude-progress.v1.json for v1'
        )
    required_v2 = ["session", "updated", "versions", "system_health", "active_tasks"]
    for field in required_v2:
        if field not in progress:
            issues.append(f"progress.json missing required field: {field}")

    sh = progress.get("system_health")
    if not isinstance(sh, list):
        issues.append("progress.json system_health must be a list (v2 typed schema)")
    else:
        for i, entry in enumerate(sh):
            if not isinstance(entry, dict):
                issues.append(f"progress.json system_health[{i}] is not a dict")
                continue
            for key in ("component", "status", "last_verified"):
                if key not in entry:
                    issues.append(f"progress.json system_health[{i}] missing {key}")

    if progress.get("active_tasks") != []:
        issues.append(
            "progress.json active_tasks must be []; TaskCreate is the source of truth now"
        )

    # --- memory hygiene ---
    memory_root = Path.home() / ".claude/projects"
    # Discover the project's memory dir by walking; skip if not a Claude Code env
    memory_dir = None
    if memory_root.exists():
        for d in memory_root.iterdir():
            if d.is_dir() and (d / "memory" / "MEMORY.md").exists():
                # heuristic: dir name contains the repo basename
                if PROJECT_ROOT.name.lower() in d.name.lower().replace("-", "_"):
                    memory_dir = d / "memory"
                    break
    if memory_dir and memory_dir.exists():
        memory_md = memory_dir / "MEMORY.md"
        lines = memory_md.read_text().splitlines()
        if len(lines) > 200:
            issues.append(
                f"memory/MEMORY.md has {len(lines)} lines; system truncates at 200. Move detail to topic files."
            )
        for lineno, ln in enumerate(lines, 1):
            if len(ln) > 250:
                issues.append(f"memory/MEMORY.md line {lineno} is {len(ln)} chars (>250)")

        # Cross-ref resolution
        referenced = set(re.findall(r"\(([a-z][a-z0-9_]*\.md)\)", memory_md.read_text()))
        for ref in referenced:
            if not (memory_dir / ref).exists():
                issues.append(f"memory/MEMORY.md references missing file: {ref}")

        # Topic-file frontmatter
        for topic in memory_dir.glob("*.md"):
            if topic.name == "MEMORY.md":
                continue
            head = topic.read_text(errors="replace").lstrip().splitlines()[:1]
            if not head or not head[0].startswith("---"):
                issues.append(f"memory/{topic.name} missing YAML frontmatter (starts with '---')")

    if issues:
        print("VALIDATION FAILED:")
        for issue in issues:
            print(f"  ✗ {issue}")
        return False
    else:
        print("Validation passed ✓")
        return True


def migrate():
    """Migrate old markdown files to archive."""
    ARCHIVE_DIR.mkdir(exist_ok=True)
    migrated = []

    for filename in DEPRECATED_FILES:
        filepath = AGENT_DIR / filename
        if filepath.exists():
            archive_path = ARCHIVE_DIR / f"deprecated-{filename}"
            shutil.move(str(filepath), str(archive_path))
            migrated.append(filename)
            print(f"Archived: {filename} → archive/deprecated-{filename}")

    # Move reference files
    REFERENCE_DIR.mkdir(exist_ok=True)
    reference_files = ["DECISIONS.md", "CONTRACTS.md", "NETWORK.md", "LAB_CREDENTIALS.md"]
    for filename in reference_files:
        filepath = AGENT_DIR / filename
        if filepath.exists() and not (REFERENCE_DIR / filename).exists():
            # Copy, don't move - keep originals for now
            shutil.copy(str(filepath), str(REFERENCE_DIR / filename))
            print(f"Copied to reference/: {filename}")

    if migrated:
        print(f"\nMigrated {len(migrated)} deprecated files to archive/")
    else:
        print("No files to migrate")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        status()

    elif cmd == "new-session":
        if len(sys.argv) < 4:
            print("Usage: new-session SESSION_NUM DESCRIPTION")
            sys.exit(1)
        new_session(int(sys.argv[2]), sys.argv[3])

    elif cmd == "end-session":
        end_session()

    elif cmd == "update":
        if len(sys.argv) < 4:
            print("Usage: update FIELD VALUE")
            print("Example: update system_health.vps_api healthy")
            sys.exit(1)
        update_field(sys.argv[2], sys.argv[3])

    elif cmd == "add-completed":
        if len(sys.argv) < 3:
            print("Usage: add-completed 'Description of completed task'")
            sys.exit(1)
        add_completed(" ".join(sys.argv[2:]))

    elif cmd == "compact":
        compact_sessions()

    elif cmd == "validate":
        if not validate():
            sys.exit(1)

    elif cmd == "migrate":
        migrate()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

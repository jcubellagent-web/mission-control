#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent
JOBS_PATH = HOME / '.hermes' / 'cron' / 'jobs.json'
CONFIG_PATH = HOME / '.hermes' / 'config.yaml'
OUT_PATH = ROOT / 'data' / 'jaimes-brain-feed.json'

RUNNING_STATES = {'running', 'in_progress', 'executing', 'working', 'active'}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


def auth_label(provider: str) -> str:
    return 'subscription' if provider == 'openai-codex' else 'api'


def normalize_model(provider: str, model: str) -> str:
    if not model:
        return 'Unknown'
    label = model.replace('gpt-', 'GPT-').replace('mini', 'Mini')
    if provider == 'openai-codex' and 'codex' not in label.lower():
        return label
    return label


def job_display(job: dict) -> str:
    return str(job.get('name') or 'JAIMES task').strip()


def main() -> int:
    now = datetime.now(timezone.utc)
    jobs = load_json(JOBS_PATH).get('jobs', [])
    cfg = load_yaml(CONFIG_PATH)

    provider = ((cfg.get('model') or {}).get('provider') or (cfg.get('agent') or {}).get('provider') or '')
    model = ((cfg.get('agent') or {}).get('model') or (cfg.get('model') or {}).get('default') or '')

    running_jobs: list[dict] = []
    recent_jobs: list[tuple[datetime, dict]] = []
    for job in jobs:
        if not job.get('enabled', True):
            continue
        state = str(job.get('state') or '').lower()
        if state in RUNNING_STATES:
            running_jobs.append(job)
        last_run = parse_ts(job.get('last_run_at'))
        if last_run and last_run >= now - timedelta(hours=6):
            recent_jobs.append((last_run, job))

    recent_jobs.sort(key=lambda item: item[0], reverse=True)
    running_jobs.sort(key=lambda job: job_display(job))

    if running_jobs:
        objective = job_display(running_jobs[0])
        active = True
        status = 'active'
        steps = [
            {
                'label': job_display(job),
                'status': 'active' if i == 0 else 'done',
                'tool': 'hermes',
            }
            for i, job in enumerate(running_jobs[:3])
        ]
        message_received = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    else:
        active = False
        status = 'idle'
        if recent_jobs:
            ts, job = recent_jobs[0]
            outcome = str(job.get('last_status') or 'ok')
            objective = f"Idle · last: {job_display(job)} ({outcome})"
            steps = [
                {
                    'label': job_display(job),
                    'status': 'done',
                    'tool': 'hermes',
                }
            ]
            message_received = ts.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        else:
            objective = 'Standby'
            steps = []
            message_received = now.strftime('%Y-%m-%dT%H:%M:%SZ')

    payload = {
        'agent': 'JAIMES',
        'active': active,
        'objective': objective,
        'status': status,
        'updatedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'checkedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'messageReceived': message_received,
        'currentTool': 'hermes' if active else '',
        'model': normalize_model(provider, model),
        'auth': auth_label(provider),
        'steps': steps,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

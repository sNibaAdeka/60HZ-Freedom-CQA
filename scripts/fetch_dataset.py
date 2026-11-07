"""Fetch a dataset from the organizers' temporary API (Power Automate GET).

python scripts/fetch_dataset.py DEV            -> data/raw/dev_records.json
python scripts/fetch_dataset.py <CONTROL_ID>   -> data/raw/<control_id>_records.json
The base URL is the "Открыть GET для DEV" link from the Student Guide with group_id replaced.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env()
BASE = os.environ.get("CQA_DATA_API", "")  # organizers' GET link without group_id; keep it in .env (not in Git)


def fetch(group_id: str) -> list[dict]:
    if not BASE:
        raise SystemExit("Set CQA_DATA_API in .env (link from the Student Guide, without group_id)")
    r = httpx.get(BASE, params={"group_id": group_id}, timeout=120)
    r.raise_for_status()
    js = r.json()
    if not js.get("isSuccess"):
        raise RuntimeError(f"API error: {js.get('error')}")
    return js["data"]["records"]


if __name__ == "__main__":
    gid = sys.argv[1] if len(sys.argv) > 1 else "DEV"
    recs = fetch(gid)
    out = Path("data/raw") / f"{gid.lower()}_records.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(recs, ensure_ascii=False, indent=1))
    print(f"{len(recs)} records -> {out}")

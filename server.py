"""CQA web prototype backend. Run:  uvicorn server:app --port 8000   (or python server.py)

Env: CQA_DATA (dialogues folder, default data/raw → falls back to data/sample), CQA_CHECKLIST (file),
     CQA_GUIDANCE (calibration notes md, default data/guidance.md), + LLM vars from cqa/llm.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from cqa import checklist as ck
from cqa import judge, llm, loader, store

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
DATA = Path(os.environ.get("CQA_DATA", "data/raw"))
if not DATA.exists() or not any(DATA.rglob("*.*")):
    DATA = Path("data/sample")
UPLOADS = Path("data/uploads")
GUIDANCE = Path(os.environ.get("CQA_GUIDANCE", "data/guidance.md"))

app = FastAPI(title="Freedom CQA")
STATE = {"dialogues": {}, "checklist": [], "progress": {"running": False, "done": 0, "total": 0, "errors": []}}


def load_all():
    ds = loader.load_dir(DATA)
    if UPLOADS.exists():
        ds += loader.load_dir(UPLOADS)
    STATE["dialogues"] = {d["id"]: d for d in ds}
    cpath = os.environ.get("CQA_CHECKLIST") or ck.find(DATA) or ck.find("data/sample")
    STATE["checklist"] = ck.load(cpath) if cpath else []
    STATE["checklist_path"] = str(cpath)
    print(f"[cqa] {len(ds)} dialogues from {DATA}, checklist {cpath} ({len(STATE['checklist'])} items), provider={llm.provider()} model={llm.model()}")


load_all()


def guidance() -> str:
    return GUIDANCE.read_text() if GUIDANCE.exists() else ""


def run_one(did: str) -> dict:
    d = STATE["dialogues"].get(did)
    if not d:
        raise HTTPException(404, "dialogue not found")
    res = judge.evaluate(d, STATE["checklist"], guidance())
    store.save_result(res)
    return res


def row_summary(d: dict, res: dict | None, rev: dict | None) -> dict:
    fin = store.final_verdicts(res, rev) if res else None
    return {
        "id": d["id"], "channel": d["channel"], "lang": d["lang"], "n_turns": len(d["turns"]),
        "preview": next((t["text"] for t in d["turns"] if t["speaker"] == "CLIENT"), d["turns"][0]["text"])[:120],
        "evaluated": res is not None,
        "ai_score": res.get("total_score") if res else None,
        "final_score": fin["final_score"] if fin else None,
        "failed": sum(1 for i in fin["items"] if i["final_verdict"] in ("no", "partial")) if fin else None,
        "critical_fail": any(i["critical"] and i["final_verdict"] in ("no", "partial") for i in fin["items"]) if fin else False,
        "needs_review": bool(res and res.get("needs_review")),
        "n_review_reasons": len(res.get("review_reasons", [])) if res else 0,
        "status": ((rev or {}).get("status") or {}).get("status"),
        "overrides": sum(1 for i in fin["items"] if i["overridden"]) if fin else 0,
        "issue": res.get("customer_issue", "") if res else "",
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "web/index.html").read_text(encoding="utf-8")


@app.get("/api/meta")
def meta():
    return {"provider": llm.provider(), "model": llm.model(), "data": str(DATA), "n_dialogues": len(STATE["dialogues"]),
            "checklist": STATE["checklist"], "checklist_path": STATE.get("checklist_path"), "guidance": bool(guidance())}


@app.get("/api/dialogues")
def dialogues():
    res, rev = store.all_results(), store.reviews()
    return [row_summary(d, res.get(i), rev.get(i)) for i, d in STATE["dialogues"].items()]


@app.get("/api/dialogues/{did}")
def dialogue(did: str):
    d = STATE["dialogues"].get(did)
    if not d:
        raise HTTPException(404)
    res = store.get_result(did)
    rev = store.reviews(did).get(did)
    return {"dialogue": {**d, "audio": f"/audio/{did}" if d.get("audio") else None}, "result": res, "review": rev,
            "final": store.final_verdicts(res, rev) if res else None}


@app.post("/api/evaluate/{did}")
def evaluate(did: str):
    try:
        return run_one(did)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"evaluation failed: {e}")


@app.post("/api/evaluate_all")
def evaluate_all(only_missing: bool = True, workers: int = 6):
    if STATE["progress"]["running"]:
        return STATE["progress"]
    done = store.all_results()
    ids = [i for i in STATE["dialogues"] if not (only_missing and i in done)]
    STATE["progress"] = {"running": True, "done": 0, "total": len(ids), "errors": []}

    def job():
        def one(i):
            try:
                run_one(i)
            except Exception as e:
                STATE["progress"]["errors"].append(f"{i}: {e}")
            STATE["progress"]["done"] += 1

        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(one, ids))
        STATE["progress"]["running"] = False

    threading.Thread(target=job, daemon=True).start()
    return STATE["progress"]


@app.get("/api/progress")
def progress():
    return STATE["progress"]


@app.post("/api/review/{did}")
def review(did: str, body: dict = Body(...)):
    store.save_review(did, str(body["item_id"]), body.get("verdict"), body.get("comment", ""), body.get("reviewer", "qa"))
    return dialogue(did)


@app.post("/api/review_status/{did}")
def review_status(did: str, body: dict = Body(...)):
    store.save_dialogue_review(did, body.get("status", "approved"), body.get("comment", ""), body.get("reviewer", "qa"))
    return dialogue(did)


@app.post("/api/upload")
def upload(body: dict = Body(...)):
    """Paste a transcript ("Оператор: ...\\nКлиент: ...") or JSON → new dialogue, evaluated immediately if evaluate=true."""
    text = body.get("text", "")
    did = body.get("id") or f"new_{uuid.uuid4().hex[:6]}"
    try:
        obj = json.loads(text)
        ds = loader._from_obj(obj, did, body.get("channel"))
    except Exception:
        ds = [loader._finalize(did, loader._turns_from_text(text), body.get("channel") or "chat")]
    if not ds or not ds[0]["turns"]:
        raise HTTPException(400, "could not parse dialogue")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    for d in ds:
        STATE["dialogues"][d["id"]] = d
        (UPLOADS / f"{d['id']}.json").write_text(json.dumps({"id": d["id"], "channel": d["channel"], "messages": [{"speaker": t["speaker"], "text": t["text"], "ts": t["ts"]} for t in d["turns"]]}, ensure_ascii=False))
    if body.get("evaluate", True):
        run_one(ds[0]["id"])
    return {"id": ds[0]["id"]}


@app.post("/api/reload")
def reload():
    load_all()
    return meta()


@app.get("/api/export.json")
def export_json():
    res, rev = store.all_results(), store.reviews()
    out = []
    for i, r in res.items():
        fin = store.final_verdicts(r, rev.get(i))
        out.append({"dialogue_id": i, "ai_score": r["total_score"], "final_score": fin["final_score"], "critical_fail": r["critical_fail"],
                    "summary": r.get("summary"), "items": [{"id": x["id"], "ai_verdict": x["verdict"], "final_verdict": x["final_verdict"], "explanation": x["explanation"], "evidence": x["evidence"], "confidence": x["confidence"], "review_comment": x["review_comment"]} for x in fin["items"]]})
    return JSONResponse(out)


@app.get("/api/export.csv")
def export_csv():
    res, rev = store.all_results(), store.reviews()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["dialogue_id", "item_id", "item_title", "ai_verdict", "final_verdict", "overridden", "confidence", "explanation", "evidence_turns", "review_comment", "ai_score", "final_score"])
    for i, r in res.items():
        fin = store.final_verdicts(r, rev.get(i))
        for x in fin["items"]:
            w.writerow([i, x["id"], x["title"], x["verdict"], x["final_verdict"], x["overridden"], x["confidence"], x["explanation"], " ".join(str(e["turn"]) for e in x["evidence"]), x["review_comment"], r["total_score"], fin["final_score"]])
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=cqa_results.csv"})


@app.get("/audio/{did}")
def audio(did: str):
    d = STATE["dialogues"].get(did)
    if not d or not d.get("audio"):
        raise HTTPException(404)
    return FileResponse(d["audio"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

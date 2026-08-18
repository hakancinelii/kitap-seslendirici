import hashlib
import json
import queue
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from book import Book, parse_book

BASE_DIR = Path(__file__).resolve().parent
BOOK_PATH = BASE_DIR / "translation_01.md"
DATA_DIR = BASE_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
STATIC_DIR = BASE_DIR / "static"

DATA_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

GRADIO_SPACE = "openbmb/VoxCPM-Demo"

book: Book = parse_book(BOOK_PATH)
seg_by_id = {s.id: s for s in book.segments}

_state_lock = threading.Lock()
status: dict[str, dict] = {}
_gen_queue: "queue.Queue[str]" = None
_client = None

reference_path: Optional[Path] = None
settings: dict = {"control": "", "cfg": 2.0, "denoise": False}
settings_lock = threading.Lock()


def _init_status():
    for s in book.segments:
        if (AUDIO_DIR / f"{s.id}.mp3").exists():
            status[s.id] = {"state": "done"}
        else:
            status[s.id] = {"state": "idle"}


def _load_reference():
    global reference_path
    if reference_path and reference_path.exists():
        return
    for p in sorted(DATA_DIR.glob("reference.*")):
        if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"):
            reference_path = p
            return


def _file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _invalidate_on_reference_change():
    """Reference ses değişirse eski seslendirmeleri temizle."""
    if reference_path is None:
        return
    hash_path = DATA_DIR / ".ref_hash"
    current = _file_sha1(reference_path)
    if hash_path.exists() and hash_path.read_text().strip() == current:
        return
    for f in AUDIO_DIR.glob("*.mp3"):
        f.unlink()
    hash_path.write_text(current)


def _set_status(seg_id: str, state: str, error: str = ""):
    with _state_lock:
        status[seg_id] = {"state": state, "error": error}


def _get_client():
    global _client
    if _client is None:
        from gradio_client import Client

        _client = Client(GRADIO_SPACE, verbose=False)
    return _client


def _call_tts(text: str) -> str:
    from gradio_client import handle_file

    client = _get_client()
    with settings_lock:
        control = settings["control"]
        cfg = settings["cfg"]
        denoise = settings["denoise"]
    ref = str(reference_path) if reference_path else None

    result = client.predict(
        text_input=text,
        control_instruction=control,
        reference_wav_path_input=handle_file(ref) if ref else None,
        use_prompt_text=False,
        prompt_text_input="",
        cfg_value_input=cfg,
        do_normalize=False,
        denoise=denoise,
        api_name="/generate",
    )
    return result


def _worker():
    while True:
        seg_id = _gen_queue.get()
        if seg_id is None:
            _gen_queue.task_done()
            break
        seg = seg_by_id.get(seg_id)
        try:
            _set_status(seg_id, "generating")
            mp3_path = None
            last_err = ""
            for attempt in range(3):
                try:
                    mp3_path = _call_tts(seg.text)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_err = str(exc)
                    time.sleep(2 * (attempt + 1))
            if mp3_path:
                dest = AUDIO_DIR / f"{seg_id}.mp3"
                shutil.move(mp3_path, dest)
                _set_status(seg_id, "done")
            else:
                _set_status(seg_id, "error", last_err or "unknown error")
        except Exception as exc:  # noqa: BLE001
            _set_status(seg_id, "error", str(exc))
        finally:
            _gen_queue.task_done()


def _enqueue_from(start_idx: int, count: Optional[int] = None) -> int:
    queued = 0
    with _state_lock:
        for s in book.segments[start_idx:]:
            if count is not None and queued >= count:
                break
            st = status.get(s.id, {}).get("state")
            if st in ("queued", "generating", "done"):
                continue
            status[s.id] = {"state": "queued", "error": ""}
            _gen_queue.put(s.id)
            queued += 1
    return queued


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _gen_queue
    _init_status()
    _load_reference()
    _invalidate_on_reference_change()
    _init_status()
    _gen_queue = queue.Queue()
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    yield
    _gen_queue.put(None)


app = FastAPI(title="Kitap Seslendirici", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/book")
def get_book():
    items = []
    for it in book.items:
        from book import Heading, Paragraph

        if isinstance(it, Heading):
            items.append({"type": "heading", "id": it.id, "level": it.level, "text": it.text})
        elif isinstance(it, Paragraph):
            items.append(
                {
                    "type": "paragraph",
                    "id": it.id,
                    "text": it.text,
                    "segment_ids": [s.id for s in it.segments],
                }
            )
    return JSONResponse(
        {
            "title": book.title,
            "items": items,
            "segments": [
                {"id": s.id, "para_id": s.para_id, "idx": s.idx, "text": s.text}
                for s in book.segments
            ],
        }
    )


@app.get("/api/settings")
def get_settings():
    with settings_lock:
        return JSONResponse(
            {
                "control": settings["control"],
                "cfg": settings["cfg"],
                "denoise": settings["denoise"],
                "reference": reference_path is not None,
                "reference_name": reference_path.name if reference_path else None,
            }
        )


@app.post("/api/settings")
async def post_settings(payload: dict):
    with settings_lock:
        if "control" in payload:
            settings["control"] = str(payload["control"])
        if "cfg" in payload:
            settings["cfg"] = float(payload["cfg"])
        if "denoise" in payload:
            settings["denoise"] = bool(payload["denoise"])
    return JSONResponse({"ok": True})


@app.post("/api/reference")
async def upload_reference(file: UploadFile = File(...)):
    global reference_path
    if not file.filename:
        raise HTTPException(status_code=400, detail="Dosya adı yok")
    ext = Path(file.filename).suffix or ".wav"
    dest = DATA_DIR / f"reference{ext}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    reference_path = dest
    return JSONResponse({"ok": True, "name": dest.name})


@app.post("/api/generate")
async def generate(payload: dict):
    start = payload.get("start")
    ids = payload.get("ids")
    if ids:
        idxs = [book.segments.index(seg_by_id[i]) for i in ids if i in seg_by_id]
        if not idxs:
            return JSONResponse({"queued": 0})
        start = min(idxs)
    if start is None:
        raise HTTPException(status_code=400, detail="start gerekli")
    count = payload.get("count")
    queued = _enqueue_from(int(start), int(count) if count is not None else None)
    return JSONResponse({"queued": queued, "total": len(book.segments)})


@app.get("/api/status")
def get_status():
    with _state_lock:
        snapshot = {k: dict(v) for k, v in status.items()}
    generating = next((k for k, v in snapshot.items() if v["state"] == "generating"), None)
    done = sum(1 for v in snapshot.values() if v["state"] == "done")
    return JSONResponse(
        {
            "segments": snapshot,
            "generating": generating,
            "done": done,
            "total": len(book.segments),
            "reference": reference_path is not None,
        }
    )


@app.get("/api/audio/{seg_id}")
def get_audio(seg_id: str):
    if seg_id not in seg_by_id:
        raise HTTPException(status_code=404, detail="Segment bulunamadı")
    path = AUDIO_DIR / f"{seg_id}.mp3"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Henüz üretilmedi")
    return FileResponse(path, media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)

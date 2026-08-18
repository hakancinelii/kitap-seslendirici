import json
import os
import time

import requests
from flask import Flask, jsonify, request

BLOB_API = "https://blob.vercel-storage.com"

BACKENDS = [
    {"url": "https://voxcpm.modelbest.cn", "voxcpm2": True},
    {"url": "https://openbmb-voxcpm-demo.hf.space", "voxcpm2": False},
]

app = Flask(__name__)


def _auth():
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN not set")
    return {"Authorization": "Bearer " + token}


def blob_list(prefix, limit=1000):
    r = requests.get(BLOB_API, params={"prefix": prefix, "limit": limit}, headers=_auth(), timeout=15)
    r.raise_for_status()
    return r.json().get("blobs", [])


def blob_put(pathname, data, content_type):
    r = requests.put(
        BLOB_API + "/" + pathname,
        headers={**_auth(), "Content-Type": content_type},
        data=data,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def _get_reference():
    refs = blob_list("reference/")
    if not refs:
        return None
    latest = max(refs, key=lambda b: b.get("uploadedAt", ""))
    url = latest.get("url")
    ext = os.path.splitext(latest.get("pathname", "ref.mp3"))[1] or ".mp3"
    data = requests.get(url, timeout=30).content
    path = f"/tmp/reference{ext}"
    with open(path, "wb") as f:
        f.write(data)
    return path


def _upload_reference(session, base, ref_path):
    with open(ref_path, "rb") as handle:
        resp = session.post(
            f"{base}/gradio_api/upload",
            files={"files": (os.path.basename(ref_path), handle, "audio/mpeg")},
            timeout=30,
        )
    resp.raise_for_status()
    uploaded_path = resp.json()[0]
    return {
        "path": uploaded_path,
        "url": f"{base}/gradio_api/file={uploaded_path}",
        "orig_name": os.path.basename(ref_path),
        "size": os.path.getsize(ref_path),
        "mime_type": "audio/mpeg",
        "meta": {"_type": "gradio.FileData"},
    }


def _generate_once(session, base, voxcpm2, ref_file, text, control):
    if voxcpm2:
        data = [text, control, ref_file, False, "", 2.0, False, False, 10, "kitap-online"]
    else:
        data = [text, control, ref_file, False, "", 2.0, False, False]
    resp = session.post(f"{base}/gradio_api/call/generate", json={"data": data}, timeout=30)
    resp.raise_for_status()
    event_id = resp.json()["event_id"]

    deadline = time.time() + 55
    while time.time() < deadline:
        stream = session.get(
            f"{base}/gradio_api/call/generate/{event_id}", stream=True, timeout=55
        )
        stream.raise_for_status()
        event_name = ""
        data_lines = []
        for raw_line in stream.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
            if time.time() >= deadline:
                raise TimeoutError("VoxCPM yanit vermedi")
        if event_name == "complete":
            if not data_lines:
                raise RuntimeError("bos sonuc")
            result = json.loads(data_lines[0])
            if not result:
                raise RuntimeError("bos sonuc")
            file_info = result[0]
            url = file_info.get("url") or f"{base}/gradio_api/file={file_info['path']}"
            audio = session.get(url, timeout=30)
            audio.raise_for_status()
            return audio.content
        if event_name == "error":
            raise RuntimeError(data_lines[0] if data_lines else "API hatasi")
        time.sleep(1)
    raise TimeoutError("VoxCPM yanit vermedi")


@app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
@app.route("/<path:path>", methods=["GET", "POST"])
def generate(path=""):
    body = request.get_json(silent=True) or {}
    seg_id = body.get("seg_id")
    text = (body.get("text") or "").strip()
    control = (body.get("control") or "").strip()
    if not seg_id or not text:
        return jsonify({"error": "seg_id ve text gerekli"}), 400

    ref_path = _get_reference()

    last_err = ""
    for cfg in BACKENDS:
        base = cfg["url"]
        try:
            session = requests.Session()
            ref_file = None
            if ref_path:
                ref_file = _upload_reference(session, base, ref_path)
            audio = _generate_once(session, base, cfg["voxcpm2"], ref_file, text, control)
            if len(audio) < 1000:
                raise RuntimeError("cok kucuk ses")
            blob = blob_put(f"audio/{seg_id}.mp3", audio, "audio/mpeg")
            return jsonify({"ok": True, "seg_id": seg_id, "url": blob.get("url")})
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)

    return jsonify({"error": last_err or "Tum backend'ler basarisiz"}), 502

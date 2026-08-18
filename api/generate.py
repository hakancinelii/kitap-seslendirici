import os
import time

import requests
from flask import Flask, jsonify, request

BLOB_API = "https://blob.vercel-storage.com"
GRADIO_SPACE = "openbmb/VoxCPM-Demo"

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


def _call_voxcpm(text, control, ref_path):
    from gradio_client import Client, handle_file

    client = Client(GRADIO_SPACE, verbose=False)
    result = client.predict(
        text_input=text,
        control_instruction=control or "",
        reference_wav_path_input=handle_file(ref_path) if ref_path else None,
        use_prompt_text=False,
        prompt_text_input="",
        cfg_value_input=2.0,
        do_normalize=False,
        denoise=False,
        api_name="/generate",
    )
    return result


@app.route("/", methods=["POST"])
def generate():
    body = request.get_json(silent=True) or {}
    seg_id = body.get("seg_id")
    text = (body.get("text") or "").strip()
    control = (body.get("control") or "").strip()
    if not seg_id or not text:
        return jsonify({"error": "seg_id ve text gerekli"}), 400

    ref_path = _get_reference()

    mp3_path = None
    last_err = ""
    for attempt in range(2):
        try:
            mp3_path = _call_voxcpm(text, control, ref_path)
            break
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            time.sleep(2 * (attempt + 1))

    if not mp3_path:
        return jsonify({"error": last_err or "VoxCPM basarisiz"}), 502

    with open(mp3_path, "rb") as f:
        data = f.read()
    blob = blob_put(f"audio/{seg_id}.mp3", data, "audio/mpeg")

    return jsonify({"ok": True, "seg_id": seg_id, "url": blob.get("url")})

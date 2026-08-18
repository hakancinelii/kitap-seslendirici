import os

import requests
from flask import Flask, jsonify

BLOB_API = "https://blob.vercel-storage.com"

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


@app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
@app.route("/<path:path>", methods=["GET", "POST"])
def status(path=""):
    audio = blob_list("audio/")
    latest: dict[str, dict] = {}
    for b in audio:
        pn = b.get("pathname", "")
        if not (pn.startswith("audio/") and pn.endswith(".mp3")):
            continue
        seg_id = pn[len("audio/"):-len(".mp3")]
        prev = latest.get(seg_id)
        if prev is None or (b.get("uploadedAt", "") >= prev.get("uploadedAt", "")):
            latest[seg_id] = b
    done = [{"seg_id": k, "url": v.get("url")} for k, v in latest.items()]

    refs = blob_list("reference/")
    reference = None
    if refs:
        newest = max(refs, key=lambda b: b.get("uploadedAt", ""))
        reference = {"url": newest.get("url"), "pathname": newest.get("pathname")}
    return jsonify({"done": done, "reference": reference})

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


@app.route("/")
def status():
    audio = blob_list("audio/")
    done = []
    for b in audio:
        pn = b.get("pathname", "")
        if pn.startswith("audio/") and pn.endswith(".mp3"):
            done.append({"seg_id": pn[len("audio/"):-len(".mp3")], "url": b.get("url")})
    refs = blob_list("reference/")
    reference = None
    if refs:
        latest = max(refs, key=lambda b: b.get("uploadedAt", ""))
        reference = {"url": latest.get("url"), "pathname": latest.get("pathname")}
    return jsonify({"done": done, "reference": reference})

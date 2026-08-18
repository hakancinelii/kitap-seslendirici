import os

import requests
from flask import Flask, jsonify, request

BLOB_API = "https://blob.vercel-storage.com"

app = Flask(__name__)


def _auth():
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN not set")
    return {"Authorization": "Bearer " + token}


def blob_put(pathname, data, content_type):
    r = requests.put(
        BLOB_API + "/" + pathname,
        headers={**_auth(), "Content-Type": content_type},
        data=data,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


@app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
@app.route("/<path:path>", methods=["GET", "POST"])
def upload_reference(path=""):
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file gerekli"}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "bos dosya"}), 400
    ext = os.path.splitext(f.filename or "")[1].lower() or ".mp3"
    content_type = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    blob = blob_put("reference/ref" + ext, data, content_type)
    return jsonify({"ok": True, "url": blob.get("url"), "pathname": blob.get("pathname")})

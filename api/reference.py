import os

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)


def _supabase():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    bucket = os.environ.get("SUPABASE_BUCKET", "audio")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL/SERVICE_KEY not set")
    return url, key, bucket


def supabase_upload(pathname, data, content_type):
    url, key, bucket = _supabase()
    r = requests.put(
        f"{url}/storage/v1/object/{bucket}/{pathname}",
        headers={"Authorization": "Bearer " + key, "apikey": key,
                 "Content-Type": content_type, "x-upsert": "true"},
        data=data,
        timeout=60,
    )
    r.raise_for_status()
    return f"{url}/storage/v1/object/public/{bucket}/{pathname}"


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
    url = supabase_upload("reference/ref" + ext, data, content_type)
    return jsonify({"ok": True, "url": url, "pathname": "reference/ref" + ext})

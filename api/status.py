import os

import requests
from flask import Flask, jsonify

app = Flask(__name__)


def _supabase():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    bucket = os.environ.get("SUPABASE_BUCKET", "audio")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL/SERVICE_KEY not set")
    return url, key, bucket


def supabase_list(prefix, limit=1000):
    url, key, bucket = _supabase()
    headers = {"Authorization": "Bearer " + key, "apikey": key, "Content-Type": "application/json"}
    out = []
    offset = 0
    while True:
        r = requests.post(
            f"{url}/storage/v1/object/list/{bucket}",
            headers=headers,
            json={"prefix": prefix, "limit": limit, "offset": offset},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json()
        if not items:
            break
        out.extend(items)
        if len(items) < limit:
            break
        offset += limit
    return out


@app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
@app.route("/<path:path>", methods=["GET", "POST"])
def status(path=""):
    url, _, bucket = _supabase()
    audio = supabase_list("audio/")
    done = []
    for b in audio:
        name = b.get("name", "")
        if not name.endswith(".mp3"):
            continue
        seg_id = name[:-len(".mp3")]
        done.append({
            "seg_id": seg_id,
            "url": f"{url}/storage/v1/object/public/{bucket}/audio/{name}",
        })

    refs = supabase_list("reference/")
    reference = None
    for b in refs:
        name = b.get("name", "")
        if name:
            reference = {
                "url": f"{url}/storage/v1/object/public/{bucket}/reference/{name}",
                "name": name,
            }
    return jsonify({"done": done, "reference": reference})

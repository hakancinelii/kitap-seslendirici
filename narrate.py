#!/usr/bin/env python3
"""Kitap seslendirme — VoxCPM ile ön-üretim + Supabase Storage yükleme.

Sesleri yerel olarak üretip Supabase Storage'a yükler. Canlı okuyucu
(/api/status) bu dosyaları görür ve hazır sesleri çalar; böylece
Vercel'in 60 sn fonksiyon limitine takılmaz.

Kullanım:
    python3 narrate.py --start 0            # tüm segmentler
    python3 narrate.py --start 0 --count 10 # ilk 10 segment
    python3 narrate.py --list-only          # segmentleri listele
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from book import parse_book, tts_cleanup  # noqa: E402

# Birinci = resmi ModelBest VoxCPM2 (10 parametre), ikinci = HF demo (8 parametre).
# NOT: Ses tutarliligi icin TEK backend kullanilir (modelbest). HF demo farkli
# model/ses urettiginden yedek olarak dahil EDILMEMISTIR.
BACKENDS = [
    {"url": "https://voxcpm.modelbest.cn", "voxcpm2": True},
]

FIXED_USER_ID = "42"


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _supabase_cfg() -> tuple[str, str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    bucket = os.environ.get("SUPABASE_BUCKET", "audio").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL/SERVICE_KEY tanimli degil (.env.local veya ortam).")
    return url, key, bucket


def supabase_upload(pathname: str, data: bytes, content_type: str = "audio/mpeg") -> str:
    url, key, bucket = _supabase_cfg()
    r = requests.put(
        f"{url}/storage/v1/object/{bucket}/{pathname}",
        headers={"Authorization": "Bearer " + key, "apikey": key,
                 "Content-Type": content_type, "x-upsert": "true"},
        data=data,
        timeout=120,
    )
    r.raise_for_status()
    return f"{url}/storage/v1/object/public/{bucket}/{pathname}"


def supabase_list(prefix: str, limit: int = 1000) -> list[dict]:
    url, key, bucket = _supabase_cfg()
    out: list[dict] = []
    offset = 0
    while True:
        r = requests.post(
            f"{url}/storage/v1/object/list/{bucket}",
            headers={"Authorization": "Bearer " + key, "apikey": key, "Content-Type": "application/json"},
            json={"prefix": prefix, "limit": limit, "offset": offset},
            timeout=30,
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


class VoxCPMBackend:
    def __init__(self, base: str, voxcpm2: bool):
        self.base = base.rstrip("/")
        self.voxcpm2 = voxcpm2
        self.session = requests.Session()
        self._ref_file: dict | None = None

    def upload_reference(self, ref_path: Path) -> None:
        with ref_path.open("rb") as handle:
            resp = self.session.post(
                f"{self.base}/gradio_api/upload",
                files={"files": (ref_path.name, handle, "audio/mpeg")},
                timeout=120,
            )
        resp.raise_for_status()
        uploaded_path = resp.json()[0]
        self._ref_file = {
            "path": uploaded_path,
            "url": f"{self.base}/gradio_api/file={uploaded_path}",
            "orig_name": ref_path.name,
            "size": ref_path.stat().st_size,
            "mime_type": "audio/mpeg",
            "meta": {"_type": "gradio.FileData"},
        }

    def generate(self, text: str, cfg: float, denoise: bool, control: str, timeout: int = 300) -> dict:
        if self._ref_file is None:
            raise RuntimeError("Referans ses yuklenmedi.")
        if self.voxcpm2:
            data = [
                text, control, self._ref_file,
                False, "", cfg, False, denoise,
                10, FIXED_USER_ID,
            ]
        else:
            data = [text, control, self._ref_file, False, "", cfg, False, denoise]

        resp = self.session.post(
            f"{self.base}/gradio_api/call/generate",
            json={"data": data},
            timeout=60,
        )
        resp.raise_for_status()
        event_id = resp.json()["event_id"]

        deadline = time.time() + timeout
        while time.time() < deadline:
            stream = self.session.get(
                f"{self.base}/gradio_api/call/generate/{event_id}",
                stream=True,
                timeout=min(120, timeout),
            )
            stream.raise_for_status()
            try:
                sock = stream.raw._fp.fp.raw._sock
                sock.settimeout(min(90, max(30, deadline - time.time())))
            except Exception:
                pass

            event_name = ""
            data_lines: list[str] = []
            for raw_line in stream.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].strip())
                if time.time() >= deadline:
                    raise TimeoutError("VoxCPM yanit vermedi.")

            if event_name == "complete":
                if not data_lines:
                    raise RuntimeError("API tamamlandi ama veri donmedi.")
                result = json.loads(data_lines[0])
                if not result:
                    raise RuntimeError("Bos sonuc.")
                return result[0]
            if event_name == "error":
                raise RuntimeError(data_lines[0] if data_lines else "API hatasi")
            time.sleep(2)
        raise TimeoutError("VoxCPM yanit vermedi.")

    def download(self, file_info: dict) -> bytes:
        url = file_info.get("url") or f"{self.base}/gradio_api/file={file_info['path']}"
        resp = self.session.get(url, timeout=120)
        resp.raise_for_status()
        return resp.content


def main() -> int:
    ap = argparse.ArgumentParser(description="Kitap seslendirme: on-uretim + Blob yukleme")
    ap.add_argument("--reference", type=Path, default=BASE_DIR / "voice_preview_nathaniel - deep, rich and mature.mp3")
    ap.add_argument("--start", type=int, default=0, help="Baslangic segment indisi")
    ap.add_argument("--end", type=int, default=None, help="Bitis segment indisi (dahil degil)")
    ap.add_argument("--count", type=int, default=None, help="Kac segment uretilecek")
    ap.add_argument("--cfg", type=float, default=2.0)
    ap.add_argument("--denoise", action="store_true")
    ap.add_argument("--control", type=str, default="", help="Ses yonergesi (opsiyonel)")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="Blob'a yukleme, sadece uret")
    args = ap.parse_args()

    _load_env(BASE_DIR / ".env.local")

    book = parse_book(BASE_DIR / "translation_01.md")
    segments = book.segments
    end = args.end if args.end is not None else len(segments)
    if args.count is not None:
        end = min(end, args.start + args.count)

    if args.list_only:
        for s in segments[args.start:end]:
            print(f"{s.id} [{len(s.text)}] {s.text[:70]}")
        print(f"Toplam {len(segments)} segment.")
        return 0

    if not args.reference.exists():
        print(f"Referans ses bulunamadi: {args.reference}", file=sys.stderr)
        return 1

    print("Supabase'teki mevcut sesler kontrol ediliyor...")
    existing = set()
    try:
        for b in supabase_list("audio/"):
            name = b.get("name", "")
            if name.endswith(".mp3"):
                existing.add(name[:-len(".mp3")])
    except Exception as exc:
        print(f"Uyari: Supabase listelenemedi ({exc}); resume kapali.", file=sys.stderr)

    print(f"Mevcut: {len(existing)} / {len(segments)}")

    backends: list[VoxCPMBackend] = []
    for cfg in BACKENDS:
        try:
            r = requests.get(cfg["url"] + "/config", timeout=30)
            r.raise_for_status()
            backends.append(VoxCPMBackend(cfg["url"], cfg["voxcpm2"]))
            print(f"Backend hazir: {cfg['url']}")
        except Exception as exc:
            print(f"Backend kapali ({cfg['url']}): {exc}")

    if not backends:
        print("Kullanilabilir VoxCPM backend yok.", file=sys.stderr)
        return 1

    max_attempts = 4
    ref_uploaded: dict[str, bool] = {}
    done_count = 0
    for idx in range(args.start, end):
        seg = segments[idx]
        if seg.id in existing:
            done_count += 1
            continue
        print(f"[{idx + 1}/{len(segments)}] {seg.id} uretiliyor... ({len(seg.text)} karakter)", flush=True)

        audio: bytes | None = None
        last_err = ""
        for attempt in range(max_attempts):
            for backend in backends:
                try:
                    if not ref_uploaded.get(backend.base):
                        print(f"  Referans yukleniyor -> {backend.base}")
                        backend.upload_reference(args.reference)
                        ref_uploaded[backend.base] = True
                    info = backend.generate(tts_cleanup(seg.text), cfg=args.cfg, denoise=args.denoise, control=args.control)
                    audio = backend.download(info)
                    if len(audio) < 1000:
                        raise RuntimeError("Cok kucuk ses dosyasi (bos sonuc)")
                    print(f"  OK -> {backend.base} ({len(audio)} byte)")
                    break
                except Exception as exc:
                    last_err = str(exc)
                    print(f"  Hata ({backend.base}): {exc}")
            if audio is not None:
                break
            wait = args.delay * (attempt + 1) * 2
            print(f"  Deneme {attempt + 1}/{max_attempts} basarisiz, {wait:.0f}s bekleniyor...")
            time.sleep(wait)

        if audio is None:
            print(f"  BASARISIZ: {seg.id} ({last_err})", file=sys.stderr)
            continue

        if not args.dry_run:
            url = supabase_upload(f"audio/{seg.id}.mp3", audio)
            print(f"  Supabase'e yuklendi: {url}")
            existing.add(seg.id)
        else:
            existing.add(seg.id)
        done_count += 1
        time.sleep(args.delay)

    print(f"\nTamamlandi. Toplam {done_count}/{len(segments)} segment hazir.")
    if done_count < len(segments):
        missing = len(segments) - done_count
        print(f"{missing} segment eksik, tekrar calistirinca yeniden denecek.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

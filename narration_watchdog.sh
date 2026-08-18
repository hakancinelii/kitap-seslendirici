#!/bin/bash
# Seslendirmeyi kesintisiz surdurur: narrate.py bitene (446/446) kadar dongu ile
# tekrar tekrar calistirir; makine uykuya dalmasin diye caffeinate kullanir.
cd "$(dirname "$0")" || exit 1

while true; do
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') basladi ==="
  caffeinate -i .venv/bin/python narrate.py --start 0
  code=$?
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') bitti (exit=$code) ==="
  if [ "$code" -eq 0 ]; then
    echo "TUM SEGMENTLER TAMAMLANDI."
    break
  fi
  echo "Eksik segmentler var, 10 sn sonra tekrar deneniyor..."
  sleep 10
done

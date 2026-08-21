#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt -r requirements-local.txt
python local_studio.py

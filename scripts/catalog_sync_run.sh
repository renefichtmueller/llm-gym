#!/bin/bash
cd "$(dirname "$0")/.."
echo "=== $(date) catalog-sync ==="
.venv/bin/python -c "from llm_gym import config, catalog_sync; import json; print(json.dumps(catalog_sync.sync(config.load_settings())))"

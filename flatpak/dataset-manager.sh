#!/bin/sh
export PYTHONPATH=/app/lib/dataset-manager${PYTHONPATH:+:$PYTHONPATH}
exec python3 -m dataset_manager "$@"

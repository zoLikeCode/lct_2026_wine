#!/bin/bash
# Code update only. Never overwrites live annotations, credentials, or images.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
destination="${WINE_SSH_TARGET:-root@77.105.169.21}"
rsync -az "$project_dir/app.py" "$project_dir/product.py" "$project_dir/collection.py" "$project_dir/ml_client.py" "$project_dir/gigachat_client.py" "$project_dir/exports.py" "$project_dir/annotation_formats.py" "$project_dir/duplicates.py" "$project_dir/data_store.py" "$project_dir/inference.py" "$project_dir/ranking_core.py" "$destination:/opt/wine/wine_server/"
rsync -az "$project_dir/content/" "$destination:/opt/wine/wine_server/content/"
rsync -az "$project_dir/static/" "$destination:/opt/wine/wine_server/static/"
rsync -az "$project_dir/scripts/verify_dataset.py" "$project_dir/scripts/backup_metadata.py" "$destination:/opt/wine/wine_server/scripts/"
rsync -az "$project_dir/../wine_curation/curation.py" "$destination:/opt/wine/wine_curation/"
ssh "$destination" 'systemctl restart wine'

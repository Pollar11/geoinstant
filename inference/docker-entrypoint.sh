#!/bin/sh
# Mounted disks are often root-owned: create the data dirs, hand them to the app user, then drop root.
set -e
if [ "$(id -u)" = "0" ]; then
  for d in "$GEOINSTANT_ARCHIVE_DIR" "$GEOINSTANT_FEEDBACK_DIR" "$GEOINSTANT_DEM_DIR" "$GEOINSTANT_STREETMATCH_DIR" "$GEOINSTANT_ARTIFACTS_DIR"; do
    [ -n "$d" ] && mkdir -p "$d" && chown -R app "$d" 2>/dev/null || true
  done
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"

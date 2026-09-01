#!/bin/sh
# Optional Docker entrypoint. Does not change how `fr serve` works on a host.
set -eu

DATA="${FR_DATA_DIR:-/data}"
PACK="${FR_PACK_PATH:-/opt/framework-reader/content.sqlite}"
HOME_DIR="${FRAMEWORK_READER_HOME:-$DATA/home}"
CONTENT_DB="${FR_CONTENT_DB:-$DATA/content.sqlite}"
SECRET_FILE="$DATA/.fr_secret_key"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$HOME_DIR"
    chown -R app:app "$DATA"
    exec gosu app "$0" "$@"
fi

mkdir -p "$HOME_DIR"

if [ ! -f "$CONTENT_DB" ]; then
    if [ ! -f "$PACK" ]; then
        echo "No content pack at $PACK and nothing at $CONTENT_DB." >&2
        echo "Rebuild the image, or mount a content.sqlite at $CONTENT_DB." >&2
        exit 1
    fi
    cp "$PACK" "$CONTENT_DB"
fi

if [ -z "${FR_SECRET_KEY:-}" ]; then
    if [ -f "$SECRET_FILE" ]; then
        FR_SECRET_KEY=$(cat "$SECRET_FILE")
        export FR_SECRET_KEY
    else
        FR_SECRET_KEY=$(python -c "from framework_reader.crypto import new_master_key; print(new_master_key())")
        umask 077
        printf '%s' "$FR_SECRET_KEY" > "$SECRET_FILE"
        export FR_SECRET_KEY
        echo "Generated FR_SECRET_KEY and stored it on the data volume."
        echo "In production, inject FR_SECRET_KEY from a secrets manager instead."
    fi
fi

export FR_CONTENT_DB="$CONTENT_DB"
export FRAMEWORK_READER_HOME="$HOME_DIR"

cd /app
if [ "$#" -eq 0 ]; then
    set -- fr serve --host 0.0.0.0 --port 8765 --db "$CONTENT_DB"
fi
exec "$@"

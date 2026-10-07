#!/usr/bin/env sh

# Create the first, local-only Quay administrator. The generated password stays
# in .env and the API token returned by Quay is deliberately discarded.
set -eu

root_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
environment_file="$root_dir/.env"
endpoint='http://localhost:8443/api/v1/user/initialize'

if [ ! -f "$environment_file" ]; then
  printf '%s\n' 'Run scripts/bootstrap.sh before initializing the local Quay administrator.' >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$environment_file"

status_code="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --header 'Content-Type: application/json' \
  --data "{\"username\":\"syntara-admin\",\"password\":\"$QUAY_ADMIN_PASSWORD\",\"email\":\"syntara-admin@localhost\",\"access_token\":true}" \
  "$endpoint")"

case "$status_code" in
  200|201)
    printf '%s\n' 'Created the local syntara-admin account.'
    ;;
  400|409)
    printf '%s\n' 'A local Quay account already exists; no administrator was created.'
    ;;
  *)
    printf '%s\n' "Quay administrator initialization failed with HTTP $status_code." >&2
    exit 1
    ;;
esac

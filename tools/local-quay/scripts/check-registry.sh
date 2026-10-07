#!/usr/bin/env sh

# Verify that loopback-only local-PoC Quay exposes its OCI Distribution endpoint.
set -eu

endpoint='http://localhost:8443/v2/'

headers="$(curl --silent --show-error --dump-header - --output /dev/null "$endpoint")"

if ! printf '%s\n' "$headers" | grep -q '^HTTP/.* 401'; then
  printf '%s\n' 'Local Quay did not return the expected OCI authentication challenge.' >&2
  exit 1
fi

if ! printf '%s\n' "$headers" | grep -qi '^www-authenticate: Bearer realm="http://localhost:8443/v2/auth"'; then
  printf '%s\n' 'Local Quay did not advertise the expected local-PoC token endpoint.' >&2
  exit 1
fi

printf '%s\n' "Local Quay is ready at $endpoint"

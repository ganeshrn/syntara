#!/usr/bin/env sh

# Create local-only Quay credentials and the rendered Quay configuration.
# This is the explicitly unsafe loopback-only local-PoC profile; it does not
# generate a CA or signing material. The generated files stay owner-readable.
set -eu

root_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
environment_file="$root_dir/.env"
template_file="$root_dir/config/config.yaml.template"
rendered_config_file="$root_dir/config/config.yaml"

umask 077

if [ ! -f "$environment_file" ]; then
  postgres_password="$(openssl rand -hex 32)"
  quay_admin_password="$(openssl rand -hex 32)"
  database_secret_key="$(openssl rand -hex 32)"
  quay_secret_key="$(openssl rand -hex 32)"
  printf '%s\n' \
    "POSTGRES_PASSWORD=$postgres_password" \
    "QUAY_ADMIN_PASSWORD=$quay_admin_password" \
    "QUAY_DATABASE_SECRET_KEY=$database_secret_key" \
    "QUAY_SECRET_KEY=$quay_secret_key" >"$environment_file"
fi

# Support local stacks created before the initial-admin helper was added.
if ! grep -q '^QUAY_ADMIN_PASSWORD=' "$environment_file"; then
  printf '%s\n' "QUAY_ADMIN_PASSWORD=$(openssl rand -hex 32)" >>"$environment_file"
fi

# shellcheck disable=SC1090
. "$environment_file"

sed \
  -e "s/__POSTGRES_PASSWORD__/$POSTGRES_PASSWORD/g" \
  -e "s/__QUAY_DATABASE_SECRET_KEY__/$QUAY_DATABASE_SECRET_KEY/g" \
  -e "s/__QUAY_SECRET_KEY__/$QUAY_SECRET_KEY/g" \
  "$template_file" >"$rendered_config_file"
chmod 600 "$rendered_config_file"
chmod 600 "$environment_file" "$rendered_config_file"

printf '%s\n' "Local Quay configuration is ready in $root_dir"

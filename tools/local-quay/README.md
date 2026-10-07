# Local Quay for Syntara plugin-platform development

This is an isolated, Podman-compatible local Quay stack for the Syntara plugin
catalog. It is not part of the main Syntara development compose stack. The
default `local-poc` profile creates separate PostgreSQL and artifact-storage
volumes and binds an **unsigned HTTP registry** only to `127.0.0.1:8443`.

This deliberately trades supply-chain proof for a short, repeatable local demo
path. It is unsafe outside a developer laptop: do not expose the port, point a
shared registry at this configuration, or configure it as a production Syntara
source. Production and acceptance sources retain digest verification, HTTPS,
and policy-selected signature verification. The local stack proves registry
wiring and supplies the registry dependency for catalog, API, and UI tests; it
is not evidence that the production `PinnedCosignTrustEvaluator` admits an
artifact.

## Start

```zsh
cd tools/local-quay
./scripts/bootstrap.sh
podman compose --env-file .env -f compose.yaml up -d
curl -i http://localhost:8443/v2/
./scripts/check-registry.sh
```

The final command should return a registry `401` response with a
`WWW-Authenticate: Bearer` header. That proves the OCI Distribution endpoint
is available; it does not expose a credential.

Create the initial local administrator without printing its generated password:

```zsh
./scripts/initialize-admin.sh
```

The helper is safe to rerun: once Quay contains a user it does nothing. The
generated administrator password remains in the ignored `.env` file. Log in
from Podman using the explicit loopback HTTP override:

```zsh
awk -F= '$1 == "QUAY_ADMIN_PASSWORD" { print $2 }' .env \
  | podman login --authfile podman-auth.json --tls-verify=false \
      localhost:8443 --username syntara-admin --password-stdin
```

Open `http://localhost:8443` in a browser. The endpoint is intentionally
insecure and must stay on loopback.

Repositories created by a local image push default to public in this stack. It
is intentional for the local-PoC flow and is safe only because the registry
binds to `127.0.0.1`. Do not copy this visibility setting to a shared registry.

## Push and inspect a smoke artifact

The following verifies the developer path used by the catalog proof: push a
public local image, then resolve its immutable digest. It deliberately does
not sign the image.

```zsh
podman tag docker.io/library/redis:7-alpine \
  localhost:8443/syntara-admin/plugin-platform-smoke:0.1.0
podman image push --authfile podman-auth.json --tls-verify=false \
  localhost:8443/syntara-admin/plugin-platform-smoke:0.1.0

registry='localhost:8443'
repository='syntara-admin/plugin-platform-smoke'
image="${registry}/${repository}"
digest="$(curl --silent --show-error --head \
  --header 'Accept: application/vnd.oci.image.manifest.v1+json' \
  "http://${registry}/v2/${repository}/manifests/0.1.0" \
  | awk 'tolower($0) ~ /^docker-content-digest:/ { print $2 }' \
  | tr -d '\r')"

printf '%s\n' "${image}@${digest}"
```

Keep the resulting immutable image reference in test data only. Do not treat
this result as signed or trusted by the Syntara control plane. Do not commit
`.env`, `podman-auth.json`, or the rendered `config/config.yaml`.

## Stop or reset

```zsh
cd tools/local-quay
podman compose --env-file .env -f compose.yaml down
```

To remove all local Quay data, explicitly add `-v`. This deletes the local
registry database and stored artifacts:

```zsh
podman compose --env-file .env -f compose.yaml down -v
```

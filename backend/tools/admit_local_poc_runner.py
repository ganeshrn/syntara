"""Record a local-only approved platform-runner profile revision.

This is a development bootstrap tool, not a production image-admission
workflow.  It is deliberately available only when the explicit local-PoC
switch is enabled.  Production must compose signature, provenance, scan, and
policy evaluators before a Platform Security Administrator records a profile.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from syntara.core.config.base import get_settings
from syntara.core.database.session import AsyncSessionLocal
from syntara.plugin_platform.services.runner_profile import (
    RunnerProfileRevisionCandidate,
    RunnerProfileRevisionService,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Admit one local-PoC platform runner image by immutable digest.")
    parser.add_argument(
        "--image-digest",
        required=True,
        help="Immutable OCI image reference, for example localhost:8443/syntara-admin/runners/http-v1@sha256:...",
    )
    parser.add_argument(
        "--driver",
        default="http.v1",
        choices=("http.v1",),
        help="The platform-owned driver implemented by the runner image.",
    )
    return parser.parse_args()


async def _admit(*, driver: str, image_digest: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.plugin_platform_local_poc_enabled:
        msg = "APP_PLUGIN_PLATFORM_LOCAL_POC_ENABLED=true is required for this development-only tool"
        raise RuntimeError(msg)

    candidate = RunnerProfileRevisionCandidate(
        driver=driver,
        image_digest=image_digest,
        verification_summary={
            "profile": "local-poc",
            "imageAdmission": "skipped",
            "unsafeDevelopmentMode": True,
            "reason": "local development runner bootstrap",
        },
    )
    async with AsyncSessionLocal() as session:
        result = await RunnerProfileRevisionService(session).record_admitted_revision(candidate, actor_id=None)
    return {
        "driver": result.profile.driver,
        "imageDigest": result.profile.image_digest,
        "profileRevisionId": str(result.profile.revision_id),
        "runtimeAbi": result.profile.runtime_abi,
        "idempotent": result.idempotent,
        "warning": "local-PoC only: image signature, provenance, SBOM, and scan admission were skipped",
    }


def main() -> None:
    """Parse safe command input and print a non-secret JSON result."""
    arguments = _arguments()
    print(json.dumps(asyncio.run(_admit(driver=arguments.driver, image_digest=arguments.image_digest)), sort_keys=True))


if __name__ == "__main__":
    main()

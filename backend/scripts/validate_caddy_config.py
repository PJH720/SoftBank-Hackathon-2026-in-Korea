"""Validate the rendered Fleet config with the exact locally cached Caddy image.

Run from ``backend`` with ``python -m scripts.validate_caddy_config``. This
does not pull images or contact the configured Node Pool.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app import fleet
from app.nodes import Node, Registry, Router


def main() -> int:
    docker = shutil.which("docker")
    if docker is None:
        print("Docker CLI is required; no image was installed or pulled.", file=sys.stderr)
        return 2

    image = subprocess.run(
        [docker, "image", "inspect", "--format", "{{.Id}}", fleet.CADDY_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    if image.returncode != 0 or not image.stdout.strip():
        print(
            f"Configured Caddy image {fleet.CADDY_IMAGE!r} is not available locally; "
            "the validator will not pull it.",
            file=sys.stderr,
        )
        return 2
    image_id = image.stdout.strip()
    if "@sha256:" not in fleet.CADDY_IMAGE:
        print(
            "Warning: CADDY_IMAGE is a mutable tag; compare this image ID with the "
            "production router image before treating validation as production-version proof.",
            file=sys.stderr,
        )

    reg = Registry(
        nodes=[Node(name="worker", host="192.0.2.2")],
        router=Router(name="router", host="192.0.2.1"),
    )
    app = fleet.App(
        name="validation",
        image="validation:local",
        container_port=8080,
        hostname="validation.example.test",
        replicas=[fleet.Replica("worker", "validation-1", "192.0.2.2", 8000)],
    )
    config = fleet.render_caddy_config(reg, fleet.State(apps={"validation": app}))

    with tempfile.TemporaryDirectory(prefix="cloudmorph-caddy-validate-") as temp_dir:
        config_dir = Path(temp_dir)
        (config_dir / "access-logs").mkdir(mode=0o700)
        config_path = config_dir / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        version = subprocess.run(
            [docker, "run", "--pull=never", "--rm", image_id, "caddy", "version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if version.returncode != 0:
            print(version.stderr.strip() or "Could not read the local Caddy version.", file=sys.stderr)
            return version.returncode or 1
        print(f"Validating with {fleet.CADDY_IMAGE} ({image_id[:19]}): {version.stdout.strip()}")

        validation = subprocess.run(
            [
                docker,
                "run",
                "--pull=never",
                "--rm",
                "--volume",
                f"{config_dir}:/etc/caddy",
                image_id,
                "caddy",
                "validate",
                "--config",
                "/etc/caddy/config.json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if validation.stdout:
            print(validation.stdout, end="")
        if validation.stderr:
            print(validation.stderr, file=sys.stderr, end="")
        if validation.returncode and not (validation.stdout or validation.stderr):
            print(
                f"Caddy config validation failed with exit code {validation.returncode}.",
                file=sys.stderr,
            )
        return validation.returncode


if __name__ == "__main__":
    raise SystemExit(main())

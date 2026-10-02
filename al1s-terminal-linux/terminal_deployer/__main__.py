from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from terminal_deployer.configuration import deployment_config
from terminal_deployer.models import DeploymentManifest
from terminal_deployer.prepare import prepare_host
from terminal_deployer.service import TerminalDeployer


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="al1s-terminal-deployer")
    parser.add_argument("manifest", type=Path, nargs="?")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--prepare-host",
        action="store_true",
        help="Prepare restricted upgrade paths without starting services or deploying",
    )
    mode.add_argument(
        "--recover",
        metavar="DEPLOYMENT_ID",
        help="Reconcile an interrupted deployment without re-executing it",
    )
    mode.add_argument(
        "--acceptance-passed",
        action="store_true",
        help="Confirm completed acceptance and remove this deployment's preflight DB copy",
    )
    args = parser.parse_args(argv)
    if args.prepare_host:
        if args.manifest:
            parser.error("--prepare-host does not accept a manifest")
        prepare_host(deployment_config())
        print(json.dumps({"host_upgrade_paths": "ready"}))
        return 0
    if args.recover and args.manifest:
        parser.error("--recover accepts a deployment ID, not a new manifest")
    if not args.recover and args.manifest is None:
        parser.error("manifest is required for deployment")
    deployer = TerminalDeployer(deployment_config())
    if args.recover:
        result = deployer.recover(args.recover)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["resolved"] else 2
    manifest = DeploymentManifest.from_path(args.manifest)
    if args.acceptance_passed:
        deployer.cleanup_after_acceptance(manifest.deployment_id)
        print(
            json.dumps({"deployment_id": manifest.deployment_id, "preflight_cleanup": "completed"})
        )
        return 0
    outcome = deployer.deploy(manifest)
    print(json.dumps(asdict(outcome), ensure_ascii=False, sort_keys=True))
    return 0 if outcome.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import importlib
import logging
import os
import signal
import ssl
import time
from pathlib import Path
from threading import Event, Thread
from uuid import UUID

from al1s_terminal.host_management.driver import LinuxHostDriver
from al1s_terminal.host_management.http_api import create_server
from al1s_terminal.host_management.journal import Journal
from al1s_terminal.host_management.service import HostMaintenance
from al1s_terminal.host_management.upgrades import HostUpgradeExecutor
from terminal_deployer.configuration import deployment_config
from terminal_deployer.download import PlatformReleaseClient
from terminal_deployer.service import TerminalDeployer


def main() -> None:
    fcntl = importlib.import_module("fcntl")

    root = Path(os.environ.get("AL1S_HOST_MANAGER_DATA", "/var/lib/al1s-host-manager"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "daemon.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(
            os.environ["AL1S_HOST_MANAGER_CERT"], os.environ["AL1S_HOST_MANAGER_KEY"]
        )
        token = Path(os.environ["AL1S_HOST_MANAGER_TOKEN_FILE"]).read_text().strip()
        journal = Journal(root / "commands.db")
        deployment_root = Path(os.environ.get("AL1S_DEPLOY_ROOT", "/run/media/mmcblk1p8/al1s"))
        upgrade = None
        platform_origin = os.environ.get("AL1S_UPGRADE_PLATFORM_ORIGIN")
        if platform_origin:
            terminal_id = UUID(os.environ["AL1S_UPGRADE_TERMINAL_ID"])
            upgrade = HostUpgradeExecutor(
                deployment_config(),
                lambda: PlatformReleaseClient(
                    platform_origin,
                    terminal_id,
                    token,
                    ca_file=os.environ.get("AL1S_UPGRADE_PLATFORM_CA"),
                ),
            )
        service = HostMaintenance(
            journal,
            LinuxHostDriver(
                os.environ["AL1S_HOST_CONTAINER"],
                deployment_root / "deployment-state",
                deployment_root / "logs",
            ),
            recovery=lambda identity: TerminalDeployer(deployment_config()).recover(identity),
            upgrade=upgrade,
        )
        address = (
            os.environ.get("AL1S_HOST_MANAGER_BIND", "127.0.0.1"),
            int(os.environ.get("AL1S_HOST_MANAGER_PORT", "8771")),
        )
        server = create_server(address, service, token)
        server.socket = context.wrap_socket(
            server.socket,
            server_side=True,
            do_handshake_on_connect=False,
        )
        stopping = Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda _signum, _frame: stopping.set())
        server.timeout = 0.5

        def tick() -> None:
            next_recovery_at = 0.0
            while not stopping.wait(2):
                try:
                    service.tick()
                    if time.monotonic() >= next_recovery_at:
                        next_recovery_at = time.monotonic() + 300
                        service.recover_pending()
                except Exception:
                    logging.getLogger(__name__).warning("maintenance_poll_failed")

        worker = Thread(target=tick, daemon=True)
        worker.start()
        try:
            while not stopping.is_set():
                server.handle_request()
        finally:
            stopping.set()
            server.server_close()
            worker.join(timeout=130)
            journal.close()


if __name__ == "__main__":
    main()

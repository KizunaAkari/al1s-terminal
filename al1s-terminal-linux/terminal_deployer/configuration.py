import os
from pathlib import Path

from terminal_deployer.models import HostDeploymentConfig


def deployment_config() -> HostDeploymentConfig:
    root = Path(os.getenv("AL1S_DEPLOY_ROOT", "/run/media/mmcblk1p8/al1s"))
    return HostDeploymentConfig(
        root=root,
        compose_file=Path(os.getenv("AL1S_DEPLOY_COMPOSE", root / "deployment/compose.arm64.yaml")),
        env_file=Path(os.getenv("AL1S_DEPLOY_ENV_FILE", root / "deployment/terminal.arm64.env")),
        data_dir=Path(os.getenv("AL1S_DEPLOY_DATA_DIR", root / "data")),
        adb_home=Path(os.getenv("AL1S_DEPLOY_ADB_HOME", root / "adb-home")),
        model_dir=Path(os.getenv("AL1S_DEPLOY_MODEL_DIR", root / "models")),
        cert_dir=Path(os.getenv("AL1S_DEPLOY_CERT_DIR", root / "certs")),
        container_name=os.getenv("AL1S_HOST_CONTAINER", "al1s-terminal-linux"),
    )

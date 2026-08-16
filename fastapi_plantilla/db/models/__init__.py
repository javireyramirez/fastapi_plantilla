"""fastapi_plantilla models."""

import pkgutil
from pathlib import Path


def load_all_models() -> None:
    """Load all models from db.models and modules."""
    package_dir = Path(__file__).resolve().parent

    # 1. Load legacy/global models in db.models
    for module in pkgutil.walk_packages(
        path=[str(package_dir)],
        prefix="fastapi_plantilla.db.models.",
    ):
        __import__(module.name)

    # 2. Load domain models in modules (e.g. auth, users, teams)
    modules_dir = package_dir.parent.parent / "modules"
    if modules_dir.exists():
        for module in pkgutil.walk_packages(
            path=[str(modules_dir)],
            prefix="fastapi_plantilla.modules.",
        ):
            if module.name.endswith(".models"):
                __import__(module.name)

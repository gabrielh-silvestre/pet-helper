import shutil
from pathlib import Path

FIXTURES_DIR = Path("evals") / "fixtures"


def overlay_kit(base: Path, fixture: str, dest: Path) -> None:
    source = FIXTURES_DIR / fixture
    if not source.is_dir():
        raise FileNotFoundError(f"unknown eval fixture: {fixture}")
    shutil.copytree(base, dest, dirs_exist_ok=True)
    shutil.copytree(source, dest, dirs_exist_ok=True)

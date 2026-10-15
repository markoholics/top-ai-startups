"""Load config/config.yaml and config/roles.yaml."""
import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name):
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))

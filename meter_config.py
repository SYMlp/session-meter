"""配置解析：SESSION_METER_CONFIG 环境变量 > 仓根 config.yaml > 内置默认。

config 格式刻意限制为扁平 key: value——省掉 yaml 依赖，stdlib 就能读。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEFAULTS = {
    "claude_projects_root": str(Path.home() / ".claude" / "projects"),
    "pi_sessions_root": str(Path.home() / ".pi" / "agent" / "sessions"),
    "data_dir": str(ROOT / "data"),
    "harvest_out_dir": str(ROOT / "data" / "harvest"),
}


def _parse_flat_yaml(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.split("#", 1)[0].strip().strip("'\"")
        if v:
            out[k.strip()] = v
    return out


def load() -> dict:
    cfg = dict(DEFAULTS)
    env = os.environ.get("SESSION_METER_CONFIG")
    path = Path(env) if env else ROOT / "config.yaml"
    if path.exists():
        base = path.resolve().parent
        for k, v in _parse_flat_yaml(path).items():
            p = Path(os.path.expanduser(v))
            cfg[k] = str(p if p.is_absolute() else (base / p).resolve())
    return cfg


def data_dir() -> Path:
    return Path(load()["data_dir"])

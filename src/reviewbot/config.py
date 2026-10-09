"""Per-repository configuration, read from `.reviewbot.yml` at the PR head commit."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch

import yaml

DEFAULT_IGNORE = [
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "*.min.js",
    "*.min.css",
    "*.svg",
    "*.png",
    "*.jpg",
    "*.gif",
    "*.pdf",
    "dist/*",
    "build/*",
    "vendor/*",
    "node_modules/*",
    "*_pb2.py",
    "*.snap",
]

ALL_SEVERITIES = ["security", "bug", "performance", "maintainability", "nit"]


@dataclass
class Config:
    ignore_paths: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    min_confidence: float = 0.7
    max_comments: int = 10
    # Style nitpicks are off by default: noise is what makes people uninstall review bots.
    severities: list[str] = field(
        default_factory=lambda: ["security", "bug", "performance", "maintainability"]
    )
    verify: bool = True  # second "skeptic" pass that discards false positives
    max_patch_chars: int = 30_000
    max_files: int = 25
    instructions: str = ""  # extra repo-specific guidance appended to the prompt

    def is_ignored(self, filename: str) -> bool:
        return any(fnmatch(filename, pat) or fnmatch(filename.split("/")[-1], pat) for pat in self.ignore_paths)


def parse_config(text: str | None) -> Config:
    cfg = Config()
    if not text:
        return cfg
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return cfg
    if not isinstance(data, dict):
        return cfg

    if isinstance(data.get("ignore"), list):
        base = [] if data.get("ignore_defaults") is False else DEFAULT_IGNORE
        cfg.ignore_paths = list(base) + [str(p) for p in data["ignore"]]
    if isinstance(data.get("min_confidence"), (int, float)):
        cfg.min_confidence = float(min(max(data["min_confidence"], 0.0), 1.0))
    if isinstance(data.get("max_comments"), int):
        cfg.max_comments = max(1, min(data["max_comments"], 50))
    if isinstance(data.get("severities"), list):
        cfg.severities = [s for s in data["severities"] if s in ALL_SEVERITIES] or cfg.severities
    if isinstance(data.get("verify"), bool):
        cfg.verify = data["verify"]
    if isinstance(data.get("instructions"), str):
        cfg.instructions = data["instructions"][:2000]
    return cfg

"""Configuration for reverser harness."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # S3 settings
    s3_bucket: str = ""
    s3_prefix: str = ""
    s3_region: str = "us-east-1"

    # Monitor settings
    poll_interval_seconds: int = 60

    # Analysis settings
    analysis_mode: str = "analyze"
    analysis_budget: float = 2.0
    analysis_timeout_seconds: int = 3600

    # Incus settings
    incus_image: str = "reverser-base"
    incus_profile: str = "reverser-isolated"

    # Paths
    staging_dir: str = "./staging"
    results_dir: str = "./results"
    db_path: str = "./state.db"

    # Results upload
    results_s3_prefix: str | None = None

    # Secrets (env-only)
    anthropic_api_key: str = ""

    def validate(self):
        errors = []
        if not self.s3_bucket:
            errors.append("s3_bucket is required (set in harness.toml or HARNESS_S3_BUCKET)")
        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY environment variable is required")
        if self.analysis_mode not in ("triage", "analyze", "solve"):
            errors.append(f"analysis_mode must be triage/analyze/solve, got: {self.analysis_mode}")
        if errors:
            raise ValueError("\n".join(errors))


def load_config(config_path: str | None = None) -> Config:
    """Load config from TOML file, then override with environment variables."""
    cfg = Config()

    # Load TOML file
    if config_path is None:
        config_path = os.environ.get("HARNESS_CONFIG", "harness.toml")
    path = Path(config_path)
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)

        s3 = data.get("s3", {})
        cfg.s3_bucket = s3.get("bucket", cfg.s3_bucket)
        cfg.s3_prefix = s3.get("prefix", cfg.s3_prefix)
        cfg.s3_region = s3.get("region", cfg.s3_region)

        monitor = data.get("monitor", {})
        cfg.poll_interval_seconds = monitor.get("poll_interval_seconds", cfg.poll_interval_seconds)

        analysis = data.get("analysis", {})
        cfg.analysis_mode = analysis.get("mode", cfg.analysis_mode)
        cfg.analysis_budget = analysis.get("budget", cfg.analysis_budget)
        cfg.analysis_timeout_seconds = analysis.get("timeout_seconds", cfg.analysis_timeout_seconds)

        incus = data.get("incus", {})
        cfg.incus_image = incus.get("image", cfg.incus_image)
        cfg.incus_profile = incus.get("profile", cfg.incus_profile)

        paths = data.get("paths", {})
        cfg.staging_dir = paths.get("staging_dir", cfg.staging_dir)
        cfg.results_dir = paths.get("results_dir", cfg.results_dir)
        cfg.db_path = paths.get("db_path", cfg.db_path)

        results = data.get("results", {})
        cfg.results_s3_prefix = results.get("s3_prefix", cfg.results_s3_prefix)

    # Environment variable overrides
    env_map = {
        "HARNESS_S3_BUCKET": "s3_bucket",
        "HARNESS_S3_PREFIX": "s3_prefix",
        "HARNESS_S3_REGION": "s3_region",
        "HARNESS_POLL_INTERVAL": "poll_interval_seconds",
        "HARNESS_ANALYSIS_MODE": "analysis_mode",
        "HARNESS_ANALYSIS_BUDGET": "analysis_budget",
        "HARNESS_ANALYSIS_TIMEOUT": "analysis_timeout_seconds",
        "HARNESS_INCUS_IMAGE": "incus_image",
        "HARNESS_INCUS_PROFILE": "incus_profile",
        "HARNESS_STAGING_DIR": "staging_dir",
        "HARNESS_RESULTS_DIR": "results_dir",
        "HARNESS_DB_PATH": "db_path",
        "HARNESS_RESULTS_S3_PREFIX": "results_s3_prefix",
    }

    for env_key, attr in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            current = getattr(cfg, attr)
            if isinstance(current, int):
                val = int(val)
            elif isinstance(current, float):
                val = float(val)
            setattr(cfg, attr, val)

    cfg.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    return cfg

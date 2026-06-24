"""Configuration loading from environment variables and optional config.yaml."""
import os
import logging
import yaml

logger = logging.getLogger(__name__)

# Resolve relative to project root when not running in Docker
_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.environ.get("CONFIG_FILE", os.path.join(_here, "config", "config.yaml"))


def load_config() -> dict:
    """Load config from YAML file if present, with env var overrides."""
    config = {
        "snmp_default_community": "public",
        "snmp_version": "2c",
        "poll_interval_minutes": int(os.environ.get("POLL_INTERVAL_MINUTES", 15)),
        "log_level": os.environ.get("LOG_LEVEL", "INFO"),
        "devices": [],
    }

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                file_config = yaml.safe_load(f) or {}
            config.update(file_config)
            logger.info("Loaded config from %s", CONFIG_FILE)
        except Exception as e:
            logger.warning("Failed to load config file %s: %s", CONFIG_FILE, e)

    return config


_config = None


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config

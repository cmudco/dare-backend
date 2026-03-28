"""
Environment Configuration

This module handles environment-specific configuration and feature flags
for the DARE backend. Mirrors the pattern used in the DARE frontend
(dare-frontend/src/config/environment.ts).

The ENVIRONMENT variable (from .env) determines which environment is active.

Supported environments:
- local: Local development (all features enabled)
- dare-staging: DARE staging environment
- dare-production: DARE production deployment
- gt-production: Georgia Tech production deployment
"""

import logging
from dataclasses import dataclass, field
from config import env

logger = logging.getLogger(__name__)

# Valid environment names
VALID_ENVIRONMENTS = ("local", "dare-staging", "dare-production", "gt-production")


@dataclass(frozen=True)
class FeatureFlags:
    """Backend feature flags, toggled per environment."""

    # Memory extraction scheduler (runs every 12 hours)
    enable_memory_extraction_scheduler: bool = False
    # Wallet topup scheduler (runs daily)
    enable_wallet_topup_scheduler: bool = False


@dataclass(frozen=True)
class EnvironmentConfig:
    """Complete environment configuration."""

    environment: str = "local"
    features: FeatureFlags = field(default_factory=FeatureFlags)

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    @property
    def is_dare_staging(self) -> bool:
        return self.environment == "dare-staging"

    @property
    def is_dare_production(self) -> bool:
        return self.environment == "dare-production"

    @property
    def is_gt_production(self) -> bool:
        return self.environment == "gt-production"


def _get_feature_flags(environment: str) -> FeatureFlags:
    """Get feature flags based on environment."""

    if environment == "local":
        return FeatureFlags(
            enable_memory_extraction_scheduler=True,
            enable_wallet_topup_scheduler=True,
        )

    elif environment == "dare-staging":
        return FeatureFlags(
            enable_memory_extraction_scheduler=True,
            enable_wallet_topup_scheduler=True,
        )

    elif environment == "dare-production":
        return FeatureFlags(
            enable_memory_extraction_scheduler=False,  # Disabled in production
            enable_wallet_topup_scheduler=True,         # Only wallet topup in production
        )

    elif environment == "gt-production":
        return FeatureFlags(
            enable_memory_extraction_scheduler=False,  # Disabled in GT production
            enable_wallet_topup_scheduler=True,         # Only wallet topup in GT production
        )

    # Default: conservative
    return FeatureFlags(
        enable_memory_extraction_scheduler=False,
        enable_wallet_topup_scheduler=True,
    )


def _build_config() -> EnvironmentConfig:
    """Build the complete environment configuration."""
    environment = env.ENVIRONMENT

    if environment not in VALID_ENVIRONMENTS:
        logger.warning(
            'Invalid or missing ENVIRONMENT: "%s". Defaulting to "local".',
            environment,
        )
        environment = "local"

    return EnvironmentConfig(
        environment=environment,
        features=_get_feature_flags(environment),
    )


# Singleton configuration - import this
config = _build_config()

# Convenience exports
features = config.features
is_local = config.is_local
is_dare_staging = config.is_dare_staging
is_dare_production = config.is_dare_production
is_gt_production = config.is_gt_production

# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Platform drivers for secret deployment targets."""
from __future__ import annotations

from .alibaba import AlibabaKMSDriver  # noqa: F401
from .aws_sm import AWSSecretsManagerDriver  # noqa: F401
from .aws_ssm import AWSSSMDriver  # noqa: F401
from .azure import AzureKeyVaultDriver  # noqa: F401
from .azure_devops import AzureDevOpsDriver  # noqa: F401
from .base import PlatformDriver  # noqa: F401
from .bitbucket import BitbucketPipelinesDriver  # noqa: F401
from .circleci import CircleCIDriver  # noqa: F401
from .cloudflare import CloudflarePagesDriver  # noqa: F401
from .deno import DenoDeployDriver  # noqa: F401
from .digitalocean import DigitalOceanDriver  # noqa: F401
from .docker import DockerSwarmDriver  # noqa: F401
from .flyio import FlyIODriver  # noqa: F401
from .gcp import GCPSecretManagerDriver  # noqa: F401
from .github import GitHubActionsDriver  # noqa: F401
from .gitlab import GitLabCIDriver  # noqa: F401
from .hasura import HasuraCloudDriver  # noqa: F401
from .heroku import HerokuDriver  # noqa: F401
from .huawei import HuaweiCSMSDriver  # noqa: F401
from .jdcloud import JDCloudKMSDriver  # noqa: F401
from .kubernetes import KubernetesDriver  # noqa: F401
from .laravel_forge import LaravelForgeDriver  # noqa: F401
from .local import LocalDriver  # noqa: F401
from .naver import NaverCloudDriver  # noqa: F401
from .netlify import NetlifyDriver  # noqa: F401
from .nhn import NHNCloudDriver  # noqa: F401
from .railway import RailwayDriver  # noqa: F401
from .render import RenderDriver  # noqa: F401
from .sakura import SakuraCloudDriver  # noqa: F401
from .supabase import SupabaseDriver  # noqa: F401
from .tencent import TencentSSMDriver  # noqa: F401
from .terraform import TerraformCloudDriver  # noqa: F401
from .vercel import VercelDriver  # noqa: F401
from .volcengine import VolcengineKMSDriver  # noqa: F401

DRIVER_MAP: dict[str, type[PlatformDriver]] = {
    # Cloud — Global
    "aws-secrets-manager": AWSSecretsManagerDriver,
    "aws-ssm": AWSSSMDriver,
    "azure-keyvault": AzureKeyVaultDriver,
    "gcp-secrets": GCPSecretManagerDriver,
    # Cloud — Asia
    "alibaba-kms": AlibabaKMSDriver,
    "huawei-csms": HuaweiCSMSDriver,
    "jdcloud-kms": JDCloudKMSDriver,
    "naver-cloud": NaverCloudDriver,
    "nhn-cloud": NHNCloudDriver,
    "sakura-cloud": SakuraCloudDriver,
    "tencent-ssm": TencentSSMDriver,
    "volcengine-kms": VolcengineKMSDriver,
    # PaaS / Hosting
    "cloudflare-pages": CloudflarePagesDriver,
    "digitalocean": DigitalOceanDriver,
    "flyio": FlyIODriver,
    "hasura-cloud": HasuraCloudDriver,
    "heroku": HerokuDriver,
    "laravel-forge": LaravelForgeDriver,
    "netlify": NetlifyDriver,
    "railway": RailwayDriver,
    "render": RenderDriver,
    "supabase": SupabaseDriver,
    "vercel": VercelDriver,
    "deno-deploy": DenoDeployDriver,
    # CI/CD
    "azure-devops": AzureDevOpsDriver,
    "bitbucket-pipelines": BitbucketPipelinesDriver,
    "circleci": CircleCIDriver,
    "github-actions": GitHubActionsDriver,
    "gitlab-ci": GitLabCIDriver,
    # IaC / Orchestration
    "docker-swarm": DockerSwarmDriver,
    "kubernetes": KubernetesDriver,
    "terraform-cloud": TerraformCloudDriver,
    # Local
    "local": LocalDriver,
}


def get_driver(platform: str) -> PlatformDriver:
    """Return a driver instance for the given platform name."""
    cls = DRIVER_MAP.get(platform)
    if cls is None:
        supported = ", ".join(sorted(DRIVER_MAP.keys()))
        raise ValueError(f"Unknown platform: {platform} (supported: {supported})")
    return cls()


__all__ = [
    "PlatformDriver",
    "DRIVER_MAP",
    "get_driver",
]

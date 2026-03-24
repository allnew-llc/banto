# Copyright 2025-2026 AllNew LLC
# Licensed under LicenseRef-Dual (see LICENSE)
"""Tests for banto sync platform drivers."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from banto.sync.drivers import DRIVER_MAP, PlatformDriver, get_driver
from banto.sync.drivers.aws_sm import AWSSecretsManagerDriver
from banto.sync.drivers.aws_ssm import AWSSSMDriver
from banto.sync.drivers.azure import AzureKeyVaultDriver
from banto.sync.drivers.bitbucket import BitbucketPipelinesDriver
from banto.sync.drivers.circleci import CircleCIDriver
from banto.sync.drivers.cloudflare import CloudflarePagesDriver
from banto.sync.drivers.digitalocean import DigitalOceanDriver
from banto.sync.drivers.flyio import FlyIODriver
from banto.sync.drivers.gcp import GCPSecretManagerDriver
from banto.sync.drivers.github import GitHubActionsDriver
from banto.sync.drivers.gitlab import GitLabCIDriver
from banto.sync.drivers.heroku import HerokuDriver
from banto.sync.drivers.local import GitignoreError, LocalDriver
from banto.sync.drivers.netlify import NetlifyDriver
from banto.sync.drivers.railway import RailwayDriver
from banto.sync.drivers.render import RenderDriver
from banto.sync.drivers.supabase import SupabaseDriver
from banto.sync.drivers.terraform import TerraformCloudDriver
from banto.sync.drivers.vercel import VercelDriver


class TestLocalDriver:
    @pytest.fixture
    def gitignored_dir(self, tmp_path: Path) -> Path:
        """Create a dir with .git and .gitignore covering .dev.vars"""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text(".dev.vars\n.env\n.env.*\n")
        return tmp_path

    @pytest.fixture
    def env_file(self, gitignored_dir: Path) -> Path:
        f = gitignored_dir / ".dev.vars"
        f.write_text("EXISTING_KEY=old_value\nOTHER=123\n")
        return f

    def test_exists_true(self, env_file: Path):
        driver = LocalDriver()
        assert driver.exists("EXISTING_KEY", str(env_file)) is True

    def test_exists_false(self, env_file: Path):
        driver = LocalDriver()
        assert driver.exists("NOPE", str(env_file)) is False

    def test_exists_no_file(self, tmp_path: Path):
        driver = LocalDriver()
        assert driver.exists("KEY", str(tmp_path / "missing")) is False

    def test_put_new_key(self, env_file: Path):
        driver = LocalDriver()
        assert driver.put("NEW_KEY", "new_val", str(env_file)) is True
        content = env_file.read_text()
        assert "NEW_KEY=new_val" in content
        assert "EXISTING_KEY=old_value" in content

    def test_put_update_existing(self, env_file: Path):
        driver = LocalDriver()
        assert driver.put("EXISTING_KEY", "updated", str(env_file)) is True
        content = env_file.read_text()
        assert "EXISTING_KEY=updated" in content
        assert "EXISTING_KEY=old_value" not in content

    def test_put_creates_file(self, gitignored_dir: Path):
        new_file = gitignored_dir / ".env"
        driver = LocalDriver()
        assert driver.put("KEY", "val", str(new_file)) is True
        assert new_file.exists()
        assert "KEY=val" in new_file.read_text()

    def test_put_fails_without_gitignore(self, tmp_path: Path):
        """gitignore に含まれていないファイルへの書き込みは拒否"""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text("*.log\n")
        target = tmp_path / ".dev.vars"
        driver = LocalDriver()
        with pytest.raises(GitignoreError):
            driver.put("KEY", "val", str(target))

    def test_put_quotes_special_values(self, env_file: Path):
        """改行や # を含む値はクォートされる"""
        driver = LocalDriver()
        driver.put("MULTI", "line1\nline2", str(env_file))
        content = env_file.read_text()
        assert 'MULTI="line1\\nline2"' in content

    def test_put_quotes_hash(self, env_file: Path):
        driver = LocalDriver()
        driver.put("COMMENT", "val#ue", str(env_file))
        content = env_file.read_text()
        assert 'COMMENT="val#ue"' in content

    def test_delete_existing(self, env_file: Path):
        driver = LocalDriver()
        assert driver.delete("EXISTING_KEY", str(env_file)) is True
        content = env_file.read_text()
        assert "EXISTING_KEY" not in content
        assert "OTHER=123" in content

    def test_delete_nonexistent(self, env_file: Path):
        driver = LocalDriver()
        assert driver.delete("NOPE", str(env_file)) is False

    def test_delete_no_file(self, tmp_path: Path):
        driver = LocalDriver()
        assert driver.delete("KEY", str(tmp_path / "missing")) is False

    def test_check_gitignore_found(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text(".dev.vars\n.env\n")
        assert LocalDriver.check_gitignore(str(tmp_path / ".dev.vars")) is True

    def test_check_gitignore_not_found(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text("*.log\n")
        assert LocalDriver.check_gitignore(str(tmp_path / ".dev.vars")) is False


class TestCloudflareDriver:
    @patch("banto.sync.drivers.cloudflare._find_wrangler", return_value="/usr/bin/wrangler")
    @patch("banto.sync.drivers.cloudflare.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=0, stdout="OPENAI_API_KEY\nGEMINI_API_KEY\n"
        )
        driver = CloudflarePagesDriver()
        assert driver.exists("OPENAI_API_KEY", "my-project") is True

    @patch("banto.sync.drivers.cloudflare._find_wrangler", return_value="/usr/bin/wrangler")
    @patch("banto.sync.drivers.cloudflare.subprocess.run")
    def test_exists_false(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=0, stdout="OTHER_KEY\n"
        )
        driver = CloudflarePagesDriver()
        assert driver.exists("OPENAI_API_KEY", "my-project") is False

    @patch("banto.sync.drivers.cloudflare._find_wrangler", return_value="/usr/bin/wrangler")
    @patch("banto.sync.drivers.cloudflare.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = CloudflarePagesDriver()
        assert driver.put("KEY", "val", "proj") is True
        call_args = mock_run.call_args
        assert call_args.kwargs.get("input") == "val"

    @patch("banto.sync.drivers.cloudflare._find_wrangler", return_value="/usr/bin/wrangler")
    @patch("banto.sync.drivers.cloudflare.subprocess.run")
    def test_delete_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = CloudflarePagesDriver()
        assert driver.delete("KEY", "proj") is True

    def test_exists_cli_not_found(self):
        """wrangler がない場合は False を返す"""
        with patch("banto.sync.drivers.cloudflare._find_wrangler", side_effect=FileNotFoundError):
            driver = CloudflarePagesDriver()
            assert driver.exists("KEY", "proj") is False

    def test_put_cli_not_found(self):
        """wrangler がない場合は FileNotFoundError"""
        with patch("banto.sync.drivers.cloudflare._find_wrangler", side_effect=FileNotFoundError("not found")):
            driver = CloudflarePagesDriver()
            with pytest.raises(FileNotFoundError):
                driver.put("KEY", "val", "proj")


class TestVercelDriver:
    @patch("banto.sync.drivers.vercel._find_vercel", return_value="/usr/bin/vercel")
    @patch("banto.sync.drivers.vercel.subprocess.run")
    def test_exists_true(self, mock_run, _):
        # First call = vercel link (success), second = env ls
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=0),  # link
            subprocess.CompletedProcess(
                [], returncode=0, stdout="OPENAI_API_KEY  production  encrypted\n"
            ),  # env ls
        ]
        driver = VercelDriver()
        assert driver.exists("OPENAI_API_KEY", "my-app") is True

    @patch("banto.sync.drivers.vercel._find_vercel", return_value="/usr/bin/vercel")
    @patch("banto.sync.drivers.vercel.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=0),  # link
            subprocess.CompletedProcess([], returncode=0),  # env add
        ]
        driver = VercelDriver()
        assert driver.put("KEY", "val", "app") is True
        # Second call is env add — verify --cwd is passed
        env_add_call = mock_run.call_args_list[1]
        cmd = env_add_call[0][0]
        assert "--cwd" in cmd

    @patch("banto.sync.drivers.vercel._find_vercel", return_value="/usr/bin/vercel")
    @patch("banto.sync.drivers.vercel.subprocess.run")
    def test_delete_success(self, mock_run, _):
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=0),  # link
            subprocess.CompletedProcess([], returncode=0),  # env rm
        ]
        driver = VercelDriver()
        assert driver.delete("KEY", "app") is True

    @patch("banto.sync.drivers.vercel._find_vercel", return_value="/usr/bin/vercel")
    @patch("banto.sync.drivers.vercel.subprocess.run")
    def test_put_link_fails(self, mock_run, _):
        """vercel link が失敗した場合は False"""
        mock_run.return_value = subprocess.CompletedProcess([], returncode=1)
        driver = VercelDriver()
        assert driver.put("KEY", "val", "app") is False

    def test_exists_cli_not_found(self):
        """vercel がない場合は False を返す"""
        with patch("banto.sync.drivers.vercel._find_vercel", side_effect=FileNotFoundError):
            driver = VercelDriver()
            assert driver.exists("KEY", "app") is False

    def test_put_cli_not_found(self):
        """vercel がない場合は FileNotFoundError"""
        with patch("banto.sync.drivers.vercel._find_vercel", side_effect=FileNotFoundError("not found")):
            driver = VercelDriver()
            with pytest.raises(FileNotFoundError):
                driver.put("KEY", "val", "app")


class TestGitHubActionsDriver:
    @patch("banto.sync.drivers.github._find_gh", return_value="/usr/bin/gh")
    @patch("banto.sync.drivers.github.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=0, stdout="OPENAI_API_KEY\tUpdated 2026-01-01\n"
        )
        driver = GitHubActionsDriver()
        assert driver.exists("OPENAI_API_KEY", "owner/repo") is True

    @patch("banto.sync.drivers.github._find_gh", return_value="/usr/bin/gh")
    @patch("banto.sync.drivers.github.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = GitHubActionsDriver()
        assert driver.put("KEY", "val", "owner/repo") is True
        assert mock_run.call_args.kwargs.get("input") == "val"

    @patch("banto.sync.drivers.github._find_gh", return_value="/usr/bin/gh")
    @patch("banto.sync.drivers.github.subprocess.run")
    def test_delete_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = GitHubActionsDriver()
        assert driver.delete("KEY", "owner/repo") is True


class TestHerokuDriver:
    @patch("banto.sync.drivers.heroku._find_heroku", return_value="/usr/bin/heroku")
    @patch("banto.sync.drivers.heroku.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=0, stdout='{"OPENAI_API_KEY": "..."}'
        )
        driver = HerokuDriver()
        assert driver.exists("OPENAI_API_KEY", "my-app") is True

    @patch("banto.sync.drivers.heroku._find_heroku", return_value="/usr/bin/heroku")
    @patch("banto.sync.drivers.heroku.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = HerokuDriver()
        assert driver.put("KEY", "val", "my-app") is True


class TestNetlifyDriver:
    @patch("banto.sync.drivers.netlify._find_netlify", return_value="/usr/bin/netlify")
    @patch("banto.sync.drivers.netlify.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=0, stdout="OPENAI_API_KEY=sk-...\nOTHER=123\n"
        )
        driver = NetlifyDriver()
        assert driver.exists("OPENAI_API_KEY", "my-site") is True

    @patch("banto.sync.drivers.netlify._find_netlify", return_value="/usr/bin/netlify")
    @patch("banto.sync.drivers.netlify.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = NetlifyDriver()
        assert driver.put("KEY", "val", "my-site") is True


class TestFlyIODriver:
    @patch("banto.sync.drivers.flyio._find_fly", return_value="/usr/bin/fly")
    @patch("banto.sync.drivers.flyio.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=0, stdout="OPENAI_API_KEY\t1d ago\tset\n"
        )
        driver = FlyIODriver()
        assert driver.exists("OPENAI_API_KEY", "my-app") is True

    @patch("banto.sync.drivers.flyio._find_fly", return_value="/usr/bin/fly")
    @patch("banto.sync.drivers.flyio.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = FlyIODriver()
        assert driver.put("KEY", "val", "my-app") is True


class TestAWSSSMDriver:
    @patch("banto.sync.drivers.aws_ssm._find_aws", return_value="/usr/bin/aws")
    @patch("banto.sync.drivers.aws_ssm.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = AWSSSMDriver()
        assert driver.exists("OPENAI_API_KEY", "myapp") is True
        # Verify parameter name format
        cmd = mock_run.call_args[0][0]
        assert "/myapp/OPENAI_API_KEY" in cmd

    @patch("banto.sync.drivers.aws_ssm._find_aws", return_value="/usr/bin/aws")
    @patch("banto.sync.drivers.aws_ssm.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = AWSSSMDriver()
        assert driver.put("KEY", "val", "myapp") is True
        cmd = mock_run.call_args[0][0]
        assert "--overwrite" in cmd
        assert "--type" in cmd
        assert "SecureString" in cmd

    @patch("banto.sync.drivers.aws_ssm._find_aws", return_value="/usr/bin/aws")
    @patch("banto.sync.drivers.aws_ssm.subprocess.run")
    def test_delete_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = AWSSSMDriver()
        assert driver.delete("KEY", "myapp") is True


class TestGCPSecretManagerDriver:
    @patch("banto.sync.drivers.gcp._find_gcloud", return_value="/usr/bin/gcloud")
    @patch("banto.sync.drivers.gcp.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = GCPSecretManagerDriver()
        assert driver.exists("OPENAI_API_KEY", "my-project") is True

    @patch("banto.sync.drivers.gcp._find_gcloud", return_value="/usr/bin/gcloud")
    @patch("banto.sync.drivers.gcp.subprocess.run")
    def test_put_creates_new(self, mock_run, _):
        """versions add fails -> creates new secret"""
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=1),  # versions add fails
            subprocess.CompletedProcess([], returncode=0),  # create succeeds
        ]
        driver = GCPSecretManagerDriver()
        assert driver.put("KEY", "val", "my-project") is True

    @patch("banto.sync.drivers.gcp._find_gcloud", return_value="/usr/bin/gcloud")
    @patch("banto.sync.drivers.gcp.subprocess.run")
    def test_put_adds_version(self, mock_run, _):
        """versions add succeeds -> no create needed"""
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = GCPSecretManagerDriver()
        assert driver.put("KEY", "val", "my-project") is True
        assert mock_run.call_count == 1  # only versions add called


class TestAzureKeyVaultDriver:
    @patch("banto.sync.drivers.azure._find_az", return_value="/usr/bin/az")
    @patch("banto.sync.drivers.azure.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = AzureKeyVaultDriver()
        assert driver.exists("OPENAI_API_KEY", "my-vault") is True

    @patch("banto.sync.drivers.azure._find_az", return_value="/usr/bin/az")
    @patch("banto.sync.drivers.azure.subprocess.run")
    def test_name_normalization(self, mock_run, _):
        """Underscores are converted to hyphens for Azure"""
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = AzureKeyVaultDriver()
        driver.put("OPENAI_API_KEY", "val", "my-vault")
        cmd = mock_run.call_args[0][0]
        assert "OPENAI-API-KEY" in cmd

    @patch("banto.sync.drivers.azure._find_az", return_value="/usr/bin/az")
    @patch("banto.sync.drivers.azure.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = AzureKeyVaultDriver()
        assert driver.put("KEY", "val", "my-vault") is True


class TestDigitalOceanDriver:
    @patch("banto.sync.drivers.digitalocean._find_doctl", return_value="/usr/bin/doctl")
    @patch("banto.sync.drivers.digitalocean.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=0, stdout='[{"key": "OPENAI_API_KEY", "value": "..."}]'
        )
        driver = DigitalOceanDriver()
        assert driver.exists("OPENAI_API_KEY", "app-id") is True

    @patch("banto.sync.drivers.digitalocean._find_doctl", return_value="/usr/bin/doctl")
    @patch("banto.sync.drivers.digitalocean.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = DigitalOceanDriver()
        assert driver.put("KEY", "val", "app-id") is True


class TestSupabaseDriver:
    @patch("banto.sync.drivers.supabase._find_supabase", return_value="/usr/bin/supabase")
    @patch("banto.sync.drivers.supabase.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=0, stdout="OPENAI_API_KEY\t1d ago\n"
        )
        driver = SupabaseDriver()
        assert driver.exists("OPENAI_API_KEY", "proj-ref") is True

    @patch("banto.sync.drivers.supabase._find_supabase", return_value="/usr/bin/supabase")
    @patch("banto.sync.drivers.supabase.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = SupabaseDriver()
        assert driver.put("KEY", "val", "proj-ref") is True


class TestRailwayDriver:
    @patch("banto.sync.drivers.railway._find_railway", return_value="/usr/bin/railway")
    @patch("banto.sync.drivers.railway.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = RailwayDriver()
        assert driver.put("KEY", "val", "proj-id") is True


class TestGitLabCIDriver:
    @patch("banto.sync.drivers.gitlab._find_glab", return_value="/usr/bin/glab")
    @patch("banto.sync.drivers.gitlab.subprocess.run")
    def test_put_update_then_create(self, mock_run, _):
        """update fails -> create succeeds"""
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=1),  # update fails
            subprocess.CompletedProcess([], returncode=0),  # set succeeds
        ]
        driver = GitLabCIDriver()
        assert driver.put("KEY", "val", "group/project") is True

    @patch("banto.sync.drivers.gitlab._find_glab", return_value="/usr/bin/glab")
    @patch("banto.sync.drivers.gitlab.subprocess.run")
    def test_delete_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = GitLabCIDriver()
        assert driver.delete("KEY", "group/project") is True


class TestAWSSecretsManagerDriver:
    @patch("banto.sync.drivers.aws_sm._find_aws", return_value="/usr/bin/aws")
    @patch("banto.sync.drivers.aws_sm.subprocess.run")
    def test_put_update_then_create(self, mock_run, _):
        """put-secret-value fails -> create-secret"""
        mock_run.side_effect = [
            subprocess.CompletedProcess([], returncode=1),  # put fails
            subprocess.CompletedProcess([], returncode=0),  # create succeeds
        ]
        driver = AWSSecretsManagerDriver()
        assert driver.put("KEY", "val", "myapp") is True
        assert mock_run.call_count == 2

    @patch("banto.sync.drivers.aws_sm._find_aws", return_value="/usr/bin/aws")
    @patch("banto.sync.drivers.aws_sm.subprocess.run")
    def test_exists_true(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = AWSSecretsManagerDriver()
        assert driver.exists("KEY", "myapp") is True
        cmd = mock_run.call_args[0][0]
        assert "myapp/KEY" in cmd


class TestRenderDriver:
    @patch("banto.sync.drivers.render._find_render", return_value="/usr/bin/render")
    @patch("banto.sync.drivers.render.subprocess.run")
    def test_put_success(self, mock_run, _):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        driver = RenderDriver()
        assert driver.put("KEY", "val", "srv-xxx") is True


class TestCircleCIDriver:
    @patch("banto.sync.drivers.circleci.subprocess.run")
    def test_put_success(self, mock_run):
        import os
        os.environ["CIRCLECI_TOKEN"] = "test-token"
        try:
            mock_run.return_value = subprocess.CompletedProcess(
                [], returncode=0, stdout='{"name": "KEY"}'
            )
            driver = CircleCIDriver()
            assert driver.put("KEY", "val", "github/org/repo") is True
        finally:
            del os.environ["CIRCLECI_TOKEN"]


class TestBitbucketPipelinesDriver:
    @patch("banto.sync.drivers.bitbucket.subprocess.run")
    def test_exists_true(self, mock_run):
        import os
        os.environ["BITBUCKET_USERNAME"] = "user"
        os.environ["BITBUCKET_APP_PASSWORD"] = "pass"
        try:
            mock_run.return_value = subprocess.CompletedProcess(
                [], returncode=0,
                stdout='{"values": [{"key": "MY_KEY", "uuid": "{123}"}]}'
            )
            driver = BitbucketPipelinesDriver()
            assert driver.exists("MY_KEY", "ws/repo") is True
        finally:
            del os.environ["BITBUCKET_USERNAME"]
            del os.environ["BITBUCKET_APP_PASSWORD"]


class TestTerraformCloudDriver:
    @patch("banto.sync.drivers.terraform.subprocess.run")
    def test_exists_true(self, mock_run):
        import os
        os.environ["TFE_TOKEN"] = "test-token"
        try:
            mock_run.return_value = subprocess.CompletedProcess(
                [], returncode=0,
                stdout='{"data": [{"attributes": {"key": "MY_KEY"}, "id": "var-123"}]}'
            )
            driver = TerraformCloudDriver()
            assert driver.exists("MY_KEY", "org/workspace") is True
        finally:
            del os.environ["TFE_TOKEN"]

    @patch("banto.sync.drivers.terraform.subprocess.run")
    def test_put_success(self, mock_run):
        import os
        os.environ["TFE_TOKEN"] = "test-token"
        try:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], returncode=0, stdout='{"data": []}'),  # list (delete check)
                subprocess.CompletedProcess([], returncode=0, stdout='{"id": "var-new"}'),  # create
            ]
            driver = TerraformCloudDriver()
            assert driver.put("KEY", "val", "org/ws") is True
        finally:
            del os.environ["TFE_TOKEN"]


class TestGetDriver:
    def test_all_registered_platforms(self):
        """All registered platforms return the correct driver type"""
        # Just verify DRIVER_MAP is importable and all entries instantiate
        for name in DRIVER_MAP:
            driver = get_driver(name)
            assert isinstance(driver, PlatformDriver), f"Failed for {name}"
        assert len(DRIVER_MAP) == 33

    def test_unknown_platform(self):
        with pytest.raises(ValueError, match="Unknown platform"):
            get_driver("unknown")

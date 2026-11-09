"""Offline safety decisions and CLI checks using only synthetic/local databases."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from app.ops.database_safety import (
    UnsafeDatabaseTarget, database_target_label, require_safe_admin_target,
)

REMOTE = "postgresql+psycopg://operator:do-not-print-this@production.invalid:5432/ig?secret=hidden"
ROOT = Path(__file__).resolve().parents[1]


class DatabaseSafetyTests(unittest.TestCase):
    def test_local_targets_allowed_without_metadata(self):
        for url in ("sqlite://", "postgresql://u:p@localhost/ig", "postgresql://u:p@host.docker.internal/ig_dev", "mysql+pymysql://u:p@ig-db/ig"):
            require_safe_admin_target(url, environ={})

    def test_production_metadata_overrides_local_and_conflicting_markers(self):
        for env in ({"VERCEL_ENV": "production"}, {"VERCEL_TARGET_ENV": "production", "VERCEL_ENV": "development"}):
            with self.assertRaises(UnsafeDatabaseTarget):
                require_safe_admin_target("sqlite://", environ=env)

    def test_unknown_remote_and_neon_need_two_exact_acknowledgments(self):
        for url in (REMOTE, "postgresql://u:p@arbitrary.neon.tech/ig"):
            for allow, confirm in ((False,None),(True,None),(False,database_target_label(url)),(True,"wrong")):
                with self.assertRaises(UnsafeDatabaseTarget):
                    require_safe_admin_target(url,allow_production_write=allow,confirm_database_target=confirm,environ={"VERCEL_ENV":"development"})
            require_safe_admin_target(url,allow_production_write=True,confirm_database_target=database_target_label(url),environ={})

    def test_sanitized_label_and_invalid_urls(self):
        self.assertEqual(database_target_label(REMOTE), "production.invalid:5432/ig")
        with self.assertRaises(UnsafeDatabaseTarget) as caught:
            require_safe_admin_target(REMOTE,environ={})
        for secret in ("do-not-print-this", "operator", "hidden"):
            self.assertNotIn(secret,str(caught.exception))
        for value in (None,"", "this is not a URL"):
            with self.assertRaises(UnsafeDatabaseTarget):
                require_safe_admin_target(value,environ={})

    def run_cli(self, args, url=REMOTE, **extra):
        env=os.environ.copy()
        env.update(DATABASE_URL=url,VERCEL_ENV="production",VERCEL_TARGET_ENV="production")
        env.update(extra)
        return subprocess.run([sys.executable,*args],cwd=ROOT,env=env,text=True,capture_output=True,timeout=20)

    def test_provider_refuses_before_any_prompt_or_connection(self):
        result=self.run_cli(["scripts/configure-tenant-provider.py","--provider","openai"])
        self.assertNotEqual(result.returncode,0)
        self.assertIn("Refusing administrative writes",result.stderr)
        self.assertNotIn("Enter provider credentials",result.stdout)
        self.assertNotIn("do-not-print-this",result.stdout+result.stderr)

    def test_provider_help_does_not_require_database(self):
        result=self.run_cli(["scripts/configure-tenant-provider.py","--help"],url="")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("--allow-production-write",result.stdout)

    def test_online_migration_refuses_remote_before_connecting(self):
        result=self.run_cli(["-m","alembic","upgrade","head"])
        self.assertNotEqual(result.returncode,0)
        self.assertIn("Refusing administrative writes",result.stdout+result.stderr)
        self.assertNotIn("do-not-print-this",result.stdout+result.stderr)

    def test_read_only_current_and_offline_sql_are_not_blocked(self):
        with TemporaryDirectory() as temp:
            url="sqlite:///"+str(Path(temp)/"diagnostic.db")
            result=self.run_cli(["-m","alembic","current"],url=url)
            self.assertEqual(result.returncode,0,result.stderr)
        result=self.run_cli(["-m","alembic","upgrade","06b1414cf2f0","--sql"])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("CREATE TABLE",result.stdout)

    def test_explicit_migration_override_works_on_temporary_database(self):
        with TemporaryDirectory() as temp:
            url="sqlite:///"+str(Path(temp)/"acknowledged.db")
            result=self.run_cli(["-m","alembic","-x","allow-production-write=yes","-x",
                "confirm-database-target="+database_target_label(url),"upgrade","06b1414cf2f0"],url=url)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_cloud_launchers_refuse_before_starting_consumers(self):
        for script in (ROOT/"scripts").glob("dev-cloud-*.sh"):
            with self.subTest(script=script.name), TemporaryDirectory() as temp:
                root=Path(temp)
                (root/"scripts").mkdir()
                copy=root/"scripts"/script.name
                copy.write_text(script.read_text())
                cloud=root/"cloud.env"
                cloud.write_text("DATABASE_URL='"+REMOTE+"'\nVERCEL_ENV=production\n")
                env=os.environ.copy()
                env.update(IG_CLOUD_ENV_FILE=str(cloud),IG_ADMIN_PYTHON=sys.executable,PYTHONPATH=str(ROOT))
                result=subprocess.run(["bash",str(copy)],env=env,text=True,capture_output=True,timeout=20)
                self.assertNotEqual(result.returncode,0)
                self.assertIn("Refusing administrative writes",result.stderr)
                self.assertNotIn("Starting",result.stdout)
                self.assertNotIn("do-not-print-this",result.stdout+result.stderr)

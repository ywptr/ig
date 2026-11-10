"""Offline safety decisions and CLI checks using only synthetic/local databases."""
import os
from pathlib import Path
import subprocess
import shutil
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

    def test_default_migration_accepts_production_metadata_without_write_arguments(self):
        with TemporaryDirectory() as temp:
            url="sqlite:///"+str(Path(temp)/"selected.db")
            result=self.run_cli(["-m","alembic","upgrade","06b1414cf2f0"],url=url)
            self.assertEqual(result.returncode,0,result.stderr)
            current=self.run_cli(["-m","alembic","current"],url=url)
            self.assertEqual(current.returncode,0,current.stderr)
            self.assertIn("06b1414cf2f0",current.stdout)

    def test_read_only_current_and_offline_sql_are_not_blocked(self):
        with TemporaryDirectory() as temp:
            url="sqlite:///"+str(Path(temp)/"diagnostic.db")
            result=self.run_cli(["-m","alembic","current"],url=url)
            self.assertEqual(result.returncode,0,result.stderr)
        result=self.run_cli(["-m","alembic","upgrade","06b1414cf2f0","--sql"])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("CREATE TABLE",result.stdout)

    def test_programmatic_migration_needs_no_override(self):
        with TemporaryDirectory() as temp:
            url="sqlite:///"+str(Path(temp)/"programmatic.db")
            # Alembic configures logging; isolate that normal CLI behavior from
            # unrelated application logging assertions in the parent test process.
            code=("from alembic import command; from alembic.config import Config; "
                  "command.upgrade(Config('alembic.ini'), '06b1414cf2f0')")
            result=self.run_cli(["-c",code],url=url)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue((Path(temp)/"programmatic.db").exists())

    def test_local_dotenv_default_and_explicit_environment_precedence(self):
        for exported in (False,True):
            with self.subTest(exported=exported),TemporaryDirectory() as temp:
                root=Path(temp)
                (root/"alembic/versions").mkdir(parents=True)
                shutil.copy(ROOT/"alembic.ini",root/"alembic.ini")
                shutil.copy(ROOT/"alembic/env.py",root/"alembic/env.py")
                shutil.copy(ROOT/"alembic/versions/06b1414cf2f0_create_image_requests.py",root/"alembic/versions")
                local=root/"local default.db"
                selected=root/"selected.db"
                (root/".env").write_text("DATABASE_URL='sqlite:///"+str(local).replace(" ","%20")+"'\n")
                # A production-named file must never be auto-loaded.
                (root/".env.production.local").write_text("DATABASE_URL='sqlite:///"+str(root/"must-not-open.db")+"'\n")
                env={k:v for k,v in os.environ.items() if k!="DATABASE_URL"}
                env.update(PYTHONPATH=str(ROOT),VERCEL_ENV="production")
                if exported:
                    env["DATABASE_URL"]="sqlite:///"+str(selected)
                result=subprocess.run([sys.executable,"-m","alembic","-c",str(root/"alembic.ini"),"upgrade","head"],
                    cwd=root,env=env,text=True,capture_output=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertTrue((selected if exported else local).exists())
                self.assertFalse((root/"must-not-open.db").exists())
                if exported:
                    self.assertFalse(local.exists())

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

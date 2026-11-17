"""Optional migration round-trip on an explicitly selected disposable localhost DB."""
import os
import unittest
from uuid import uuid4
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from tests import test_google_drive_storage as drive_tests

URL = os.getenv("IG_TEST_DRIVE_POSTGRES_URL")


@unittest.skipUnless(URL, "Set IG_TEST_DRIVE_POSTGRES_URL to a disposable local PostgreSQL database")
class PostgresDriveMigrationTests(drive_tests.DriveMigrationTests):
    def create_test_engine(self):
        url = make_url(URL)
        if url.host not in {"127.0.0.1", "localhost"} or url.get_backend_name() != "postgresql":
            raise ValueError("Drive migration tests require explicit disposable localhost PostgreSQL")
        schema = "drive_" + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def cleanup():
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
        self.addCleanup(cleanup)
        return create_engine(url, connect_args={"options": f"-csearch_path={schema}"})

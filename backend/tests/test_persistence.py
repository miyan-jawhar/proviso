"""Regression tests for Proviso's local durable auth and feedback storage."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.core import database
from app.main import app


class DurablePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "proviso-test.db"
        self.original_env = {
            name: os.environ.get(name)
            for name in ("DATABASE_URL", "DATABASE_URL_SYNC", "DATABASE_READONLY_URL")
        }
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{db_path.as_posix()}"
        os.environ["DATABASE_READONLY_URL"] = f"sqlite:///{db_path.as_posix()}"
        get_settings.cache_clear()
        database._engine = None
        database._session_factory = None

    def tearDown(self):
        if database._engine is not None:
            asyncio.run(database._engine.dispose())
        database._engine = None
        database._session_factory = None
        for name, value in self.original_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()
        self.temp_dir.cleanup()

    def test_registered_user_and_feedback_survive_a_new_application_context(self):
        username = "durable-user"
        password = "durable-password"

        with TestClient(app) as client:
            registration = client.post("/auth/register", json={
                "username": username,
                "password": password,
                "full_name": "Durable User",
            })
            self.assertEqual(registration.status_code, 201, registration.text)
            token = registration.json()["access_token"]

            feedback = client.post(
                "/feedback",
                json={
                    "query_text": "Did the monthly total breach the cap?",
                    "response_text": "The cited monthly total exceeded the cap.",
                    "rating": 5,
                    "comment": "Trace was clear.",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(feedback.status_code, 200, feedback.text)
            self.assertGreater(feedback.json()["feedback_id"], 0)

        with TestClient(app) as client:
            login = client.post("/auth/login", json={
                "username": username,
                "password": password,
            })
            self.assertEqual(login.status_code, 200, login.text)
            token = login.json()["access_token"]
            records = client.get(
                "/feedback",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(records.status_code, 200, records.text)
            self.assertEqual(len(records.json()["records"]), 1)
            self.assertEqual(records.json()["records"][0]["username"], username)
            self.assertEqual(records.json()["records"][0]["rating"], 5)

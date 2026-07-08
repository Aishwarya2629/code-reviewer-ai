"""
Shared pytest fixtures.

All tests run with MOCK_MODE=true so they make zero real LLM calls.
Each fixture is properly scoped to avoid cross-test contamination.
"""
import os
import pytest
from fastapi.testclient import TestClient

# Force mock mode before any app imports
os.environ["MOCK_MODE"] = "true"
os.environ["LOG_FORMAT"] = "text"
os.environ["LOG_LEVEL"] = "WARNING"


@pytest.fixture(scope="session")
def client():
    """Single TestClient for the whole test session — avoids repeated startup cost."""
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def python_code():
    return (
        "def find_duplicates(arr):\n"
        "    dupes = []\n"
        "    for i in range(len(arr)):\n"
        "        for j in range(i+1, len(arr)):\n"
        "            if arr[i] == arr[j] and arr[i] not in dupes:\n"
        "                dupes.append(arr[i])\n"
        "    return dupes"
    )


@pytest.fixture
def java_code():
    return (
        "public String buildString(String[] words) {\n"
        "    String result = \"\";\n"
        "    for (int i = 0; i < words.length; i++) {\n"
        "        result += words[i];\n"
        "    }\n"
        "    return result;\n"
        "}"
    )


@pytest.fixture
def code_with_secrets():
    return (
        "import requests\n"
        'API_KEY = "sk-abc123supersecret"\n'
        "def call_api():\n"
        '    return requests.get("https://api.example.com", headers={"key": API_KEY})\n'
    )


@pytest.fixture
def code_with_injection():
    return (
        "import sqlite3\n"
        "def get_user(username):\n"
        "    conn = sqlite3.connect('db.sqlite')\n"
        "    cursor = conn.cursor()\n"
        f"    cursor.execute(f\"SELECT * FROM users WHERE name = '{{username}}'\")\n"
        "    return cursor.fetchone()\n"
    )


@pytest.fixture
def two_sum_problem():
    return (
        "Given an array of integers nums and an integer target, "
        "return indices of the two numbers such that they add up to target. "
        "You may assume that each input would have exactly one solution."
    )

"""Demo file with deliberate bugs, used only to check that the AI reviewer comments."""

import sqlite3
import subprocess

API_KEY = "sk_live_51H8exampleSECRETkey0123456789"


def find_user(conn: sqlite3.Connection, name: str) -> list[tuple]:
    query = "SELECT * FROM users WHERE name = '" + name + "'"
    return conn.execute(query).fetchall()


def ping(host: str) -> bytes:
    return subprocess.run("ping -c 1 " + host, shell=True, capture_output=True).stdout


def last_n(items: list, n: int) -> list:
    return items[len(items) - n + 1 :]
# trivial change

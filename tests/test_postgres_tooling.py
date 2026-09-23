from __future__ import annotations

import subprocess

import pytest

from scripts.ops import postgres_tooling


def test_dump_client_rejects_old_major_and_selects_matching_client(tmp_path, monkeypatch):
    old = tmp_path / "old-pg-dump.exe"
    current = tmp_path / "current-pg-dump.exe"
    old.touch()
    current.touch()
    monkeypatch.setattr(postgres_tooling, "candidate_paths", lambda *_args: [old, current])

    def version(args, **_kwargs):
        major = 17 if args[0] == str(old) else 18
        return subprocess.CompletedProcess(args, 0, f"pg_dump (PostgreSQL) {major}.3\n", "")

    monkeypatch.setattr(postgres_tooling.subprocess, "run", version)
    path, major = postgres_tooling.resolve_tool("pg_dump", 18)
    assert path == current.resolve()
    assert major == 18


def test_dump_client_fails_closed_when_only_old_major_exists(tmp_path, monkeypatch):
    old = tmp_path / "old-pg-dump.exe"
    old.touch()
    monkeypatch.setattr(postgres_tooling, "candidate_paths", lambda *_args: [old])
    monkeypatch.setattr(
        postgres_tooling.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, "pg_dump (PostgreSQL) 17.8\n", ""
        ),
    )
    with pytest.raises(RuntimeError, match="No PostgreSQL 18 pg_dump client"):
        postgres_tooling.resolve_tool("pg_dump", 18)

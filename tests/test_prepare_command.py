"""First-use shortcut composes existing database and bounded-sync operations."""

from __future__ import annotations

from types import SimpleNamespace

from repo_troubleshooter.cli import main


def test_prepare_initialises_then_runs_the_same_bounded_sync(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(main, "db_init", lambda: calls.append(("db_init", None)))
    monkeypatch.setattr(
        main,
        "sync_cmd",
        lambda **kwargs: calls.append(("sync", kwargs)),
    )
    monkeypatch.setattr(
        main,
        "load_profile",
        lambda name: SimpleNamespace(repo=f"owner/{name}"),
    )

    main.prepare_cmd("demo", max_issues=17, no_git=True)

    assert calls[0] == ("db_init", None)
    assert calls[1] == (
        "sync",
        {
            "profile_name": "demo",
            "full": False,
            "max_discussions": None,
            "max_issues": 17,
            "max_pull_requests": 200,
            "no_docs": False,
            "no_git": True,
            "backfill_pages": 0,
            "as_json": False,
        },
    )

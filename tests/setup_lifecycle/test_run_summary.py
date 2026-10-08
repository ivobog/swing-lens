from app.services.setup_lifecycle.run_summary import setup_lifecycle_run_summary


class SummaryDb:
    def __init__(self) -> None:
        self.values = iter((209, 96, 1194, 165))
        self.statements = []

    def scalar(self, statement):
        compiled = statement.compile()
        self.statements.append((str(statement), compiled.params))
        return next(self.values)


def test_run_summary_separates_bookkeeping_from_meaningful_changes() -> None:
    db = SummaryDb()

    summary = setup_lifecycle_run_summary(db, 30)  # type: ignore[arg-type]

    assert summary == {
        "active_episode_count": 165,
        "canonical_revision_count": 209,
        "lifecycle_change_count": 96,
        "signal_change_count": 1194,
        "meaningful_change_count": 1290,
    }
    assert "CANONICAL_REVISION" in db.statements[0][1].values()
    assert "EPISODE_OPENED" in next(
        value for value in db.statements[1][1].values() if isinstance(value, list)
    )
    assert "signal_change_events" in db.statements[2][0]
    assert "current_snapshot_id" in db.statements[3][0]

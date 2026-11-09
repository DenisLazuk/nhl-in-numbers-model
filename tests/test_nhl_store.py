from nhl_store import init_schema, table_counts


def test_init_schema_creates_empty_tables(tmp_path):
    db = tmp_path / "nhl.db"
    assert init_schema(db) == db
    counts = table_counts(db)
    assert counts["teams"] == 0
    assert counts["games"] == 0
    assert "projected_goalies" in counts
    assert "injury_flags" in counts

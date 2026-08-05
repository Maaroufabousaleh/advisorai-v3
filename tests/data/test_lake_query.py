from advisorai.contracts import ArtifactTier
from advisorai.lake import DataLake, LakeQuery


def test_lake_query_is_read_only_over_parquet(tmp_path, observation):
    lake = DataLake(tmp_path / "lake")
    lake.write_observations(tier=ArtifactTier.SILVER, dataset="market", observations=(observation,))
    query = LakeQuery(tmp_path / "lake")
    rows = query.scan("silver/market/**/*.parquet")
    assert rows.height == 1
    try:
        query.sql("CREATE TABLE forbidden(x INTEGER)")
    except PermissionError:
        pass
    else:
        raise AssertionError("LakeQuery unexpectedly allowed mutation")
    with __import__("pytest").raises(PermissionError, match="outside"):
        query.scan("../outside/**/*.parquet")
    with __import__("pytest").raises(PermissionError, match="outside"):
        query.sql("SELECT * FROM read_parquet('../outside/data.parquet')")

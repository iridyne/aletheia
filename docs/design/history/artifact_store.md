# Retired ArtifactStore

`core/aletheia/storage/artifact_store.py` (`ArtifactStore`, `get_artifact_store()`,
and the `PSArtifactRef` model) was a filesystem store under `<data_dir>/artifacts/`
with `put_json/put_yaml/put_text/put_jsonl`, `verify`, and `make_manifest`.

It was deleted because no production code in `core`, `extensions`, or `ops` ever
called it; only its own unit test and an existence-only spec did. The persistence
Contract nevertheless listed it as an entry point with "lessons, skills, policies,
and operator-facing readers" and stated that artifact manifests identify immutable
content. Its `_atomic_write` replaced an existing path unconditionally, so a second
write to the same path invalidated any earlier manifest hash; the immutability claim
held only in tests.

Lessons, skills, and policies persist through SQLite repositories and the
append-only `EventStore`. A content-addressed artifact store that returns needs a
real producer and consumer plus a Contract entry backed by a behavioral test.

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aletheia.storage.artifact_store import ArtifactStore
from aletheia.storage.hashing import sha256_bytes


def test_concurrent_writes_to_same_path_use_independent_staging_files(
    tmp_path,
    monkeypatch,
):
    store = ArtifactStore(tmp_path)
    target = "shared.txt"
    payloads = ("a" * 10_000, "b" * 10_000)
    publish_barrier = threading.Barrier(2)
    staging_paths: list[Path] = []
    staging_paths_lock = threading.Lock()

    def synchronized_publish(original):
        def publish(source, destination, *args, **kwargs):
            with staging_paths_lock:
                staging_paths.append(Path(source))
            publish_barrier.wait(timeout=2)
            return original(source, destination, *args, **kwargs)

        return publish

    monkeypatch.setattr(os, "rename", synchronized_publish(os.rename))
    monkeypatch.setattr(os, "replace", synchronized_publish(os.replace))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.put_text, target, payload) for payload in payloads]
        refs = [future.result(timeout=3) for future in futures]

    final_bytes = (tmp_path / target).read_bytes()
    expected_hashes = {sha256_bytes(payload.encode()) for payload in payloads}

    assert {ref.sha256 for ref in refs} == expected_hashes
    assert sha256_bytes(final_bytes) in expected_hashes
    assert final_bytes in {payload.encode() for payload in payloads}
    assert len(set(staging_paths)) == 2
    assert list(tmp_path.glob(".shared.txt.*.tmp")) == []

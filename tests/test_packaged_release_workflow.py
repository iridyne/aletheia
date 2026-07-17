from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build-packaged-runtime.yml"


def test_manual_publish_builds_a_signed_public_pages_feed():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "publish:" in source
    assert "type: boolean" in source
    assert "ALETHEIA_UPDATE_SIGNING_PRIVATE_KEY_PEM" in source
    assert "https://iridite.github.io/aletheia/update-feed.json" in source
    assert "--public-key-output" in source
    assert "--public-key" in source
    assert "--feed-url-file" in source
    assert 'tar -C "$PWD/dist/update-site" -cvf "$RUNNER_TEMP/artifact.tar" .' in source
    assert "uses: actions/upload-artifact@v4" in source
    assert "name: github-pages" in source
    assert "path: ${{ runner.temp }}/artifact.tar" in source
    assert "actions/deploy-pages@v4" in source
    assert "pages: write" in source
    assert "id-token: write" in source
    assert source.count('"self-hosted","macOS","ARM64","aletheia-release"') == 2
    assert "runs-on: [self-hosted, macOS, ARM64, aletheia-release]" in source
    assert source.count('ALETHEIA_PACKAGED_SMOKE: "1"') == 2
    assert source.count(
        "enable-cache: ${{ github.event_name != 'workflow_dispatch' || !inputs.publish }}"
    ) == 2
    assert 'ALETHEIA_SMOKE_DASHBOARD_PORT: "18787"' in source
    assert 'ALETHEIA_SMOKE_DASHBOARD_PORT: "19787"' in source
    assert "scheduler_enabled: false" in source


def test_pull_request_build_never_publishes_the_release_site():
    source = WORKFLOW.read_text(encoding="utf-8")

    publish_guard = "github.event_name == 'workflow_dispatch' && inputs.publish"
    assert source.count(publish_guard) >= 4

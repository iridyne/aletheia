import pytest
from typer.testing import CliRunner

from aletheia.cli.inspect_cmd import inspect_app


@pytest.mark.parametrize(
    "args",
    [
        ["predictions", "typo"],
        ["predictions", "events-nope", "--json"],
        ["serenity", "theme"],
        ["serenity", "unknown", "--json"],
    ],
)
def test_unknown_inspect_selector_fails_before_repository_access(args):
    result = CliRunner().invoke(inspect_app, args)

    assert result.exit_code == 2
    assert "must be one of" in result.output

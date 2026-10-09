"""In-memory stand-in for ``aletheia.config.loader.edit_config`` in route tests."""

from __future__ import annotations

from contextlib import contextmanager


def stub_config_edits(monkeypatch, module, cfg, on_save=None) -> None:
    """Route ``module.load_config``/``module.edit_config`` to one in-memory config.

    ``on_save(cfg)`` runs when an edit block exits normally with a change, the
    same point at which the real ``edit_config`` persists.
    """
    monkeypatch.setattr(module, "load_config", lambda: cfg)

    @contextmanager
    def edit_config():
        before = cfg.model_dump(mode="json")
        yield cfg
        if on_save is not None and cfg.model_dump(mode="json") != before:
            on_save(cfg)

    monkeypatch.setattr(module, "edit_config", edit_config)

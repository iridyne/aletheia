# Current task entry point

Do not treat a shared Markdown page as live task state.

Run:

```bash
uv run aletheia agent workflow bootstrap
uv run aletheia doctor
```

The first command selects the active task packet or clearly labels the latest
completed task as history. The second command probes machine-local runtime
facts. Long-form planning history remains in `docs/_ai/CURRENT_TASK.md`.

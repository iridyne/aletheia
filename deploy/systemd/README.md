# Fedora systemd units for the Observation Worker

Repo-native templates for the sibling Observer checkout (Issue #1072). These
are copied by hand — no automated installer in the first patch.

## First-time install on Fedora

Assumes:
- `/home/admin/aletheia` is the Main Runtime checkout (already there).
- `/home/admin/aletheia-observer` is a fresh sibling checkout of the same
  repo, deliberately pinned to a different SHA than Main.
- Both share `~/.aletheia`.

```sh
# 1. Bootstrap the Worker checkout (once).
git -C /home/admin clone git@github.com:iridyne/aletheia.git aletheia-observer
cd /home/admin/aletheia-observer
uv sync

# 2. Flip ownership to worker in the shared config.
#    ~/.aletheia/config.yaml — add:
#
#    observation:
#      execution_owner: worker

# 3. Install the systemd user units.
mkdir -p ~/.config/systemd/user
cp deploy/systemd/aletheia-observer.service ~/.config/systemd/user/
cp deploy/systemd/aletheia-observer.timer   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now aletheia-observer.timer

# 4. Prove the split.
#    Main scheduler must no longer register filter_controller_observe:
curl -s http://127.0.0.1:8787/api/scheduler/jobs | grep filter_controller_observe   # → empty
#    Worker timer fires:
systemctl --user list-timers | grep aletheia-observer
#    One manual pass:
systemctl --user start aletheia-observer.service
journalctl --user -u aletheia-observer.service -n 20
```

## Watching for Worker drift

The Worker checkout is deliberately pinned — that is the whole point of the
split. But over months a pinned SHA can accumulate real distance from
master, and the operator should be able to check without leaving Fedora:

```sh
# From inside the Worker checkout — offline, uses local origin/master ref.
cd /home/admin/aletheia-observer && .venv/bin/aletheia observe drift

# Refresh the origin ref first for a live comparison.
cd /home/admin/aletheia-observer && .venv/bin/aletheia observe drift --fetch

# Non-zero exit when the Worker is more than N commits behind master
# (default 30) — wire into any monitoring you like.
cd /home/admin/aletheia-observer && .venv/bin/aletheia observe drift --fetch --warn-after 50 || echo "worker drift alert"
```

Drift is advisory. The Worker is never auto-updated by this command; the
operator decides when to `git pull` and restart the timer.

## Rollback (no data migration)

```sh
systemctl --user disable --now aletheia-observer.timer
# Edit ~/.aletheia/config.yaml: observation.execution_owner: main
# Restart Main Gateway — reconcile re-adds filter_controller_observe.
```

## Explicit non-goals

- No automatic Worker checkout provisioning. The operator picks the SHA.
- No credential sharing with the Worker. The service file explicitly
  clears exchange env vars.
- No web/dashboard/gateway startup from the Worker unit.
- No auto-update on the Worker. Its whole reason to exist is being
  independently pinned.

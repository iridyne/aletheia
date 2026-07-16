"""Human-triggered software update support.

This package is not exposed to the agent tool registry. Installed runtimes use signed
immutable payloads; development checkouts retain the explicit Git updater.
"""


def _install_packaged_integrity_contract() -> None:
    # Keep the large staging module focused on orchestration while one implementation
    # owns archive/link/manifest integrity. Import-time installation also protects legacy
    # direct callers of the low-level module; Web/CLI still use packaged_commands.
    from aletheia.update import packaged
    from aletheia.package_integrity import extract_archive, validate_release_dir

    def compatible_extract_archive(archive, staging):
        try:
            return extract_archive(archive, staging)
        except packaged.PackagedUpdateError as exc:
            if "symlink" in str(getattr(exc, "reason", "")):
                raise packaged.PackagedUpdateError(
                    exc.reason,
                    f"unsupported symbolic link: {exc}",
                ) from exc
            raise

    def safe_request_update():
        from aletheia.update.packaged_commands import request_update

        return request_update()

    def safe_request_rollback():
        from aletheia.update.packaged_commands import request_rollback

        return request_rollback()

    packaged._extract_archive = compatible_extract_archive
    packaged._validate_release_dir = validate_release_dir
    packaged.request_update = safe_request_update
    packaged.request_rollback = safe_request_rollback


_install_packaged_integrity_contract()
del _install_packaged_integrity_contract

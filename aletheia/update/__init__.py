"""Human-triggered software update support.

This package is not exposed to the agent tool registry. Installed runtimes use signed
immutable payloads; development checkouts retain the explicit Git updater.
"""


def _install_packaged_integrity_contract() -> None:
    # Keep the large staging module focused on orchestration while one implementation
    # owns archive/link/manifest integrity. Import-time installation also protects legacy
    # direct callers of the low-level module; Web/CLI still use packaged_commands.
    from aletheia.update import packaged
    from aletheia.update.package_integrity import extract_archive, validate_release_dir

    packaged._extract_archive = extract_archive
    packaged._validate_release_dir = validate_release_dir


_install_packaged_integrity_contract()
del _install_packaged_integrity_contract

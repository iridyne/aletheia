"""Gateway 启动前预检，确保必要条件满足。

验证项：
- 静态文件完整性（dashboard_static/ 存在且非空）
- 数据库可访问
- 配置文件有效
- 端口未被占用（由 lifecycle.py 已处理）

失败时给出明确的修复建议，而不是让 Gateway 静默启动然后 404。
"""

import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger("aletheia")

PreflightStatus = Literal["pass", "warn", "fail"]


class PreflightCheck:
    """单个预检项的结果。"""

    def __init__(
        self,
        name: str,
        status: PreflightStatus,
        message: str,
        remedy: str | None = None,
    ):
        self.name = name
        self.status = status
        self.message = message
        self.remedy = remedy  # 修复建议

    def __repr__(self):
        return f"<PreflightCheck {self.name}: {self.status}>"


class PreflightResult:
    """所有预检项的聚合结果。"""

    def __init__(self):
        self.checks: list[PreflightCheck] = []

    def add(
        self,
        name: str,
        status: PreflightStatus,
        message: str,
        remedy: str | None = None,
    ):
        self.checks.append(PreflightCheck(name, status, message, remedy))

    @property
    def passed(self) -> bool:
        """所有检查都 pass（warn 不算失败）。"""
        return all(c.status != "fail" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warn" for c in self.checks)

    def failures(self) -> list[PreflightCheck]:
        return [c for c in self.checks if c.status == "fail"]

    def warnings(self) -> list[PreflightCheck]:
        return [c for c in self.checks if c.status == "warn"]


def check_static_files() -> PreflightCheck:
    """检查 dashboard_static/ 是否完整。

    CRITICAL: 缺失会导致 WebUI 404，但 API 仍可工作。Gateway 会启动，
    但用户看到空白页/JSON 404，难以排查。
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    static_dir = project_root / "aletheia" / "dashboard_static"

    if not static_dir.exists():
        return PreflightCheck(
            "static_files",
            "fail",
            f"Static directory missing: {static_dir}",
            remedy=(
                "Run: cd frontend && npm install && npm run build\n"
                "This rebuilds aletheia/dashboard_static/. "
                "Commit the built files to avoid this on other machines."
            ),
        )

    index_html = static_dir / "index.html"
    if not index_html.exists():
        return PreflightCheck(
            "static_files",
            "fail",
            f"index.html missing in {static_dir}",
            remedy=(
                "Run: cd frontend && npm run build\n"
                "Vite may have failed or only partially built. Check frontend/package.json."
            ),
        )

    # 检查 assets/ 目录（Vite 把 JS/CSS 放这里）
    assets_dir = static_dir / "assets"
    if not assets_dir.exists() or not list(assets_dir.glob("*.js")):
        return PreflightCheck(
            "static_files",
            "warn",
            f"assets/ directory empty or missing JS bundles in {static_dir}",
            remedy="Run: cd frontend && npm run build",
        )

    # 通过 — 检查文件大小确保不是空壳
    index_size = index_html.stat().st_size
    if index_size < 100:
        return PreflightCheck(
            "static_files",
            "warn",
            f"index.html suspiciously small ({index_size} bytes)",
            remedy="Rebuild may have failed mid-way. Run: cd frontend && npm run build",
        )

    return PreflightCheck(
        "static_files",
        "pass",
        f"Static files OK: {static_dir} ({index_size} bytes index.html)",
    )


def check_database() -> PreflightCheck:
    """检查数据库是否可访问（基本连接测试）。"""
    try:
        from aletheia.storage.sqlite import get_db

        db = get_db()
        # 简单查询验证连接
        db.execute("SELECT 1").fetchone()
        return PreflightCheck("database", "pass", "Database accessible")
    except Exception as e:
        return PreflightCheck(
            "database",
            "fail",
            f"Database connection failed: {e}",
            remedy="Check data/app.db permissions and disk space. Run: uv run aletheia doctor",
        )


def check_config() -> PreflightCheck:
    """检查配置文件是否可加载。"""
    try:
        from aletheia.config.loader import load_config

        cfg = load_config()
        # 验证关键字段
        if not cfg.app.dashboard_port:
            raise ValueError("app.dashboard_port not set")
        return PreflightCheck("config", "pass", "Configuration valid")
    except Exception as e:
        return PreflightCheck(
            "config",
            "fail",
            f"Configuration load failed: {e}",
            remedy="Check data/config.yaml syntax. Run: uv run aletheia doctor",
        )


def run_preflight_checks() -> PreflightResult:
    """执行所有预检项，返回聚合结果。"""
    result = PreflightResult()

    # 按优先级顺序检查（config 先于 db，因为 db 可能依赖 config）
    result.add(**check_config().__dict__)
    result.add(**check_database().__dict__)
    result.add(**check_static_files().__dict__)

    return result


def log_preflight_result(result: PreflightResult):
    """将预检结果输出到日志（用于 CI/调试）。"""
    if result.passed and not result.has_warnings:
        logger.info("Preflight checks: ALL PASS")
        return

    if result.has_warnings:
        logger.warning("Preflight checks: WARNINGS detected")
        for check in result.warnings():
            logger.warning(f"  [{check.name}] {check.message}")
            if check.remedy:
                logger.warning(f"    Fix: {check.remedy}")

    if not result.passed:
        logger.error("Preflight checks: FAILURES detected")
        for check in result.failures():
            logger.error(f"  [{check.name}] {check.message}")
            if check.remedy:
                logger.error(f"    Fix: {check.remedy}")


def assert_preflight_or_exit(result: PreflightResult):
    """如果预检失败，打印报告并退出进程（用于 CLI 启动）。"""
    if result.passed:
        return

    import sys

    print("\n=== Gateway Preflight Failed ===")
    for check in result.failures():
        print(f"\n[FAIL] {check.name}: {check.message}")
        if check.remedy:
            print(f"  → Fix: {check.remedy}")

    if result.has_warnings:
        print("\n--- Warnings ---")
        for check in result.warnings():
            print(f"[WARN] {check.name}: {check.message}")
            if check.remedy:
                print(f"  → Suggest: {check.remedy}")

    print("\nGateway startup aborted. Fix the issues above and try again.")
    sys.exit(1)

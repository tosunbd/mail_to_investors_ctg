"""Configuration loading.

Two files in the project root:
  * settings.toml - everything that is not a secret (servers, subjects, pacing...)
  * .env          - secrets only (DB_USER, DB_PASSWORD, SMTP_USER, SMTP_PASSWORD)

Real environment variables override .env, so a scheduler or a Linux service
can inject secrets without touching files.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """Raised when settings.toml / .env is missing or invalid."""


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    service_name: str
    sid: str
    user: str
    password: str
    mode: str  # "thick" (Instant Client, required for Oracle 10g/11g) or "thin"
    client_dir: Path | None
    connect_retries: int
    connect_retry_delay_seconds: int

    @property
    def dsn_display(self) -> str:
        target = f"service {self.service_name}" if self.service_name else f"SID {self.sid}"
        return f"{self.user}@{self.host}:{self.port} ({target})"


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str  # "starttls" | "ssl" | "none"
    user: str
    password: str
    from_address: str
    timeout_seconds: int
    delay_between_emails_ms: int
    batch_size: int
    batch_pause_seconds: int
    max_retries: int
    retry_initial_delay_seconds: float
    retry_max_delay_seconds: float


@dataclass(frozen=True)
class JobSettings:
    subject: str
    include_logo: bool
    attachment_name: str = ""


@dataclass(frozen=True)
class RunSettings:
    dry_run: bool
    test_recipient: str
    test_max_emails: int
    skip_already_sent_today: bool
    admin_email: str
    keep_output_days: int


@dataclass(frozen=True)
class Paths:
    root: Path
    templates: Path
    assets: Path
    output: Path
    logs: Path

    @property
    def pdf(self) -> Path:
        return self.output / "pdf"

    @property
    def reports(self) -> Path:
        return self.output / "reports"

    @property
    def dry_run(self) -> Path:
        return self.output / "dry_run"

    @property
    def state(self) -> Path:
        return self.output / "state"

    @property
    def logo(self) -> Path:
        return self.assets / "icb_logo.jpg"


@dataclass(frozen=True)
class Settings:
    database: DatabaseSettings
    smtp: SmtpSettings
    portfolio: JobSettings
    sale_purchase: JobSettings
    run: RunSettings
    paths: Paths


def _read_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser (comments with #, optional quotes)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for lineno, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{path.name} line {lineno}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _secret(name: str, env_file: dict[str, str], required: bool = True) -> str:
    value = os.environ.get(name, env_file.get(name, "")).strip()
    if required and not value:
        raise ConfigError(f"{name} is not set (put it in .env or the environment)")
    return value


def _optional_address(value: object) -> str:
    """"" / "null" / "none" / "off" all mean: no test address (send to investors)."""
    text = str(value or "").strip()
    return "" if text.lower() in ("", "null", "none", "off", "no", "false") else text


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_settings(root: Path = PROJECT_ROOT) -> Settings:
    settings_file = root / "settings.toml"
    if not settings_file.exists():
        raise ConfigError(
            f"Settings file not found: {settings_file} - "
            "copy settings.example.toml to settings.toml and fill it in (or run setup_windows.bat)"
        )
    try:
        with settings_file.open("rb") as fh:
            cfg = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"settings.toml is invalid: {exc}") from exc

    env_file = _read_env_file(root / ".env")

    try:
        db = cfg["database"]
        smtp = cfg["smtp"]
        pf = cfg["portfolio"]
        sp = cfg["sale_purchase"]
        run = cfg["run"]
        paths = cfg.get("paths", {})
    except KeyError as exc:
        raise ConfigError(f"settings.toml is missing the [{exc.args[0]}] section") from exc

    mode = str(db.get("mode", "thick")).lower()
    if mode not in ("thick", "thin"):
        raise ConfigError("database.mode must be 'thick' or 'thin'")
    service_name = str(db.get("service_name", "")).strip()
    sid = str(db.get("sid", "")).strip()
    if not service_name and not sid:
        raise ConfigError("database: set either service_name or sid")
    client_dir = str(db.get("client_dir", "")).strip()

    security = str(smtp.get("security", "starttls")).lower()
    if security not in ("starttls", "ssl", "none"):
        raise ConfigError("smtp.security must be 'starttls', 'ssl' or 'none'")

    database = DatabaseSettings(
        host=str(db["host"]),
        port=int(db.get("port", 1521)),
        service_name=service_name,
        sid=sid,
        user=_secret("DB_USER", env_file),
        password=_secret("DB_PASSWORD", env_file),
        mode=mode,
        client_dir=_resolve(root, client_dir) if client_dir else None,
        connect_retries=int(db.get("connect_retries", 3)),
        connect_retry_delay_seconds=int(db.get("connect_retry_delay_seconds", 10)),
    )
    smtp_settings = SmtpSettings(
        host=str(smtp["host"]),
        port=int(smtp.get("port", 587)),
        security=security,
        user=_secret("SMTP_USER", env_file),
        password=_secret("SMTP_PASSWORD", env_file),
        from_address=str(smtp.get("from_address", "")).strip() or _secret("SMTP_USER", env_file),
        timeout_seconds=int(smtp.get("timeout_seconds", 120)),
        delay_between_emails_ms=int(smtp.get("delay_between_emails_ms", 400)),
        batch_size=max(0, int(smtp.get("batch_size", 300))),
        batch_pause_seconds=max(0, int(smtp.get("batch_pause_seconds", 60))),
        max_retries=max(0, int(smtp.get("max_retries", 5))),
        retry_initial_delay_seconds=float(smtp.get("retry_initial_delay_seconds", 2)),
        retry_max_delay_seconds=float(smtp.get("retry_max_delay_seconds", 30)),
    )
    portfolio = JobSettings(
        subject=str(pf.get("subject", "Portfolio Statement From ICB")),
        include_logo=bool(pf.get("include_logo", True)),
        attachment_name=str(pf.get("attachment_name", "Portfolio.pdf")),
    )
    sale_purchase = JobSettings(
        subject=str(sp.get("subject", "Sale/Purchase Confirmation From ICB")),
        include_logo=bool(sp.get("include_logo", True)),
    )
    run_settings = RunSettings(
        dry_run=bool(run.get("dry_run", False)),
        test_recipient=_optional_address(run.get("test_recipient")),
        test_max_emails=max(1, int(run.get("test_max_emails", 1))),
        skip_already_sent_today=bool(run.get("skip_already_sent_today", True)),
        admin_email=str(run.get("admin_email", "")).strip(),
        keep_output_days=int(run.get("keep_output_days", 60)),
    )
    path_settings = Paths(
        root=root,
        templates=_resolve(root, paths.get("templates", "templates")),
        assets=_resolve(root, paths.get("assets", "assets")),
        output=_resolve(root, paths.get("output", "output")),
        logs=_resolve(root, paths.get("logs", "logs")),
    )
    return Settings(
        database=database,
        smtp=smtp_settings,
        portfolio=portfolio,
        sale_purchase=sale_purchase,
        run=run_settings,
        paths=path_settings,
    )

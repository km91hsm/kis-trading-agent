"""accounts 기능 선언."""

from trader.features.accounts.cli import accounts_app
from trader.features.accounts.migrations import MIGRATIONS
from trader.features.accounts.ports import PORT
from trader.features.accounts.settings import SECTION
from trader.features.accounts.telegram import STATUS_SECTION, TELEGRAM_COMMANDS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="accounts",
    tier="service",
    sections=(SECTION,),
    migrations=MIGRATIONS,
    cli=accounts_app,
    routes=(),  # 웹 자원은 M11
    telegram_commands=TELEGRAM_COMMANDS,
    status_sections=(STATUS_SECTION,),
    control_namespaces=("paused",),
    ports=(PORT,),
)

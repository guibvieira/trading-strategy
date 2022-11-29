"""Command-line entry point for the daemon build on the top of Typer."""

from .commands.app import app
from .commands.check_universe import check_universe
from .commands.check_wallet import check_wallet
from .commands.hello import hello
from .commands.start import start
from .commands.perform_test_trade import perform_test_trade


# Dummy export commands even though they are already registered
# to make the linter happy
__all__ = [app, check_wallet, check_universe, hello, start, perform_test_trade]











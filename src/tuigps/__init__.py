"""tuigps - Terminal UI GPS monitor using gpsd."""

import logging

__version__ = "0.1.0"

# Library-style logging: never print to the terminal the TUI is drawing on.
logging.getLogger(__name__).addHandler(logging.NullHandler())

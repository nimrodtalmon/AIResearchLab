"""Agent registry. Importing this module wires every implemented role into the
dispatcher's registry."""
from . import dummy  # noqa: F401  — registers role at import time

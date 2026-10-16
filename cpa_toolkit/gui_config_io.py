"""Rewrite individual ``NAME = "..."`` lines in config.py in place.

Used by the setup GUI's "save to config.py" action. Only touches the
matched lines via regex; every comment and every other setting is left
exactly as it was -- config.py's comments are the documentation for each
field, and a silent append (or a full-file rewrite) would orphan a value
from its explanation.
"""

from __future__ import annotations

import pathlib
import re

CONFIG_PATH = pathlib.Path(__file__).with_name("config.py")


def save_config_values(**kv: str) -> None:
    """Rewrite the named string assignments; raise if a name isn't found."""
    text = CONFIG_PATH.read_text()
    for name, value in kv.items():
        pattern = re.compile(r'^(%s\s*=\s*)"[^"]*"' % re.escape(name), re.MULTILINE)
        if not pattern.search(text):
            raise KeyError('config.py has no `%s = "..."` line to update' % name)
        text = pattern.sub(lambda m, v=value: m.group(1) + '"%s"' % v, text, count=1)
    CONFIG_PATH.write_text(text)

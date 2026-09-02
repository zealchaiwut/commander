"""Helpers for temporary sys.modules purges that restore afterward (issue #2345).

Fixtures that need a fresh ``import server`` historically purged every
``services.*`` key. That rebuilds module objects under new ``id()``s, so later
tests that bound ``services.sprint_manager.*`` at collection time see
monkeypatches miss (issues #2337 / #2345).

Use ``temporary_module_purge`` so the polluting fixture still gets a clean
import, then the original module objects are put back for the rest of the suite.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterable, Iterator


@contextmanager
def temporary_module_purge(
    *names: str,
    prefixes: Iterable[str] = (),
) -> Iterator[None]:
    """Pop matching modules for the duration of the ``with`` block, then restore.

    Newly-imported modules matching ``names``/``prefixes`` that were not in the
    snapshot are discarded on exit so the restored originals win.
    """
    prefix_tuple = tuple(prefixes)
    saved: dict[str, object] = {}
    for mod in list(sys.modules):
        if mod in names or any(mod.startswith(p) for p in prefix_tuple):
            saved[mod] = sys.modules.pop(mod)
    try:
        yield
    finally:
        for mod in list(sys.modules):
            if mod in names or any(mod.startswith(p) for p in prefix_tuple):
                if mod not in saved:
                    sys.modules.pop(mod, None)
        sys.modules.update(saved)

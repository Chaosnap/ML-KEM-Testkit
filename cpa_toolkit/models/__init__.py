"""CPA leakage models, one module per model family.

Every module in this folder that defines ``MODELS = [Model(...), ...]`` is loaded
automatically; cpa.py, the CLI (``--list-models``) and the GUI then offer its models.

    models/base.py   Model base class, hw12 / hw24 / hd helpers
    models/HW.py     Hamming weight of the multiplier registers (hw_product_mod, hw_product_raw)

To add a family, for example Hamming distance, create ``models/HD.py``::

    from models.base import Model, hd

    class HDModel(Model):
        def hypotheses(self, u, guesses):
            ...   # return an (N, G) float32 array

    MODELS = [HDModel("hd_...", "title", "equation", "register", 24, (10, 11), "source")]

Model names must be unique over all modules.
"""

from __future__ import annotations

import importlib
import pkgutil

from models.base import Model

DEFAULT_MODEL = "hw_product_mod"


def _load() -> dict[str, Model]:
    found: dict[str, Model] = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name == "base" or info.name.startswith("_"):
            continue
        module = importlib.import_module("models." + info.name)
        for m in getattr(module, "MODELS", []):
            if m.name in found:
                raise ValueError("model %r is defined twice (models/%s.py)" % (m.name, info.name))
            found[m.name] = m
    return found


MODELS: dict[str, Model] = _load()


def get_model(name: str) -> Model:
    if name not in MODELS:
        raise ValueError("unknown model %r (available: %s)" % (name, ", ".join(MODELS)))
    return MODELS[name]


def list_models() -> list[dict]:
    return [m.describe() for m in MODELS.values()]

"""Leakage models for the CPA attack, one file per model.

Every module in this folder that ends with ``MODEL = <LeakageModel subclass>()``
is a model, named after its file: ``cpa_attack.py --model <name>``, the GUI's
Analysis page and the reports all use that name. Shared code (the BaseMul
intermediates, R_prev, Hamming weight / distance, the base class and the
self-check) is in ``base.py``; ``_template.py`` shows how to write a new
model. Files starting with ``_`` are not models.

The GUI re-reads this folder whenever it lists the models, so a new or edited
file shows up without restarting the server.
"""

from __future__ import annotations

import importlib
import os
import pkgutil

from .base import REGISTERS, RPREVS, Guess, LeakageModel, PipelineModel, check, hd, hw  # noqa: F401

#: Names the models had as mlkem_ref.MODEL_PRESETS (older reports, configs, scripts).
ALIASES = {"HW": "hw_scalar", "HD": "hd_scalar", "basemul-c0": "hd_basemul_c0",
           "basemul-c1": "hd_basemul_c1", "basemul-word": "hd_basemul_word"}

#: Older reports made with a free target/R_prev ("custom"): the model on that register.
BY_REGISTER = {"p_self": "hd_scalar", "c0": "hd_basemul_c0", "c1": "hd_basemul_c1", "word": "hd_basemul_word"}

_NOT_MODELS = {"base"}


def names() -> list[str]:
    """Model names: the files in this folder (without ``_*`` and ``base``)."""
    return sorted(m.name for m in pkgutil.iter_modules(__path__)
                  if not m.name.startswith("_") and m.name not in _NOT_MODELS)


def resolve(name: str = "", register: str = "") -> str:
    """A model name from a current name, an old preset name, or (old "custom"
    reports) the register; "" if none of them names a model."""
    name = ALIASES.get(name, name)
    if name in names():
        return name
    return BY_REGISTER.get(register, "")


def get(name: str, reload: bool = False) -> LeakageModel:
    """The model in ``models/<name>.py`` (old preset names accepted)."""
    name = ALIASES.get(name, name)
    if name not in names():
        raise ValueError("no such leakage model: %r (models: %s)" % (name, ", ".join(names())))
    mod = importlib.import_module("%s.%s" % (__name__, name))
    if reload:
        mod = importlib.reload(mod)
    model = getattr(mod, "MODEL", None)
    if not isinstance(model, LeakageModel):
        raise ValueError("models/%s.py does not end with MODEL = <LeakageModel subclass>()" % name)
    if model.register not in REGISTERS:
        raise ValueError("models/%s.py: register %r is not one of %s" % (name, model.register, ", ".join(REGISTERS)))
    unknown = [r for r in model.rprevs if r not in RPREVS]
    if unknown:
        raise ValueError("models/%s.py: unknown R_prev %s" % (name, ", ".join(unknown)))
    model.name, model.source = name, mod.__file__
    return model


def scan() -> tuple[list[LeakageModel], list[dict]]:
    """Every model, re-read from disk; a file that fails to load is reported, not raised."""
    found, errors = [], []
    for name in names():
        try:
            found.append(get(name, reload=True))
        except Exception as e:  # noqa: BLE001 - a broken file must not hide the others
            errors.append({"file": "models/%s.py" % name, "error": "%s: %s" % (type(e).__name__, e)})
    return found, errors


def describe(model: LeakageModel) -> dict:
    """Everything the GUI shows about a model."""
    return {"name": model.name, "file": "models/" + os.path.basename(model.source), "title": model.title,
            "family": model.family, "register": model.register, "bits": model.bits,
            "rprevs": [{"name": r, "desc": RPREVS[r]} for r in model.rprevs],
            "equation": model.equation, "rtl_register": model.rtl_register,
            "pipeline_stage": model.pipeline_stage, "required_inputs": model.required_inputs,
            "limitations": model.limitations, "aligned": model.aligned, "doc": (model.__doc__ or "").strip()}


def selftest(model: LeakageModel) -> list[dict]:
    """The FIPS 203 chain the models build on, then the model itself (base.check)."""
    import mlkem_ref as R
    import testvectors as TV
    seed = os.urandom(64)
    vs = TV.generate("tvla", 2, seed)
    row = int((vs.cls == 0).argmax())
    ok = bool(R.selftest(vs.dk, vs.ct[row].tobytes(), R.fixed_message(seed)))
    rows = [{"name": "FIPS 203 chain (û, ŝ, NTT, BaseMul)", "ok": ok,
             "detail": "software decryption recovers the encapsulated message" if ok
             else "software decryption is wrong: no CPA result can be trusted"}]
    return rows + [{"name": n, "ok": o, "detail": d} for n, o, d in check(model)]

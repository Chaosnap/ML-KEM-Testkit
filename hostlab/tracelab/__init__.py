"""tracelab - host-side measurement framework for the pqc-testkit ML-KEM core.

Layers, bottom to top::

    protocol.Link      framed UART link to the board (CRC32, registers, buffer)
    target.Target      ML-KEM-768 operations and bring-up checks
    vectors.VectorSet  shuffled fixed/random inputs from `pqc-testkit sca gen`
    scope.Scope        instrument control (VisaScope, NullScope)
    campaign.Campaign  the acquisition loop, with integrity checking
    storage.TraceSet   on-disk trace set (plain .npy + meta.json)
    analysis           Welch t-test over the captured traces

Each layer is usable on its own: ``Target`` without a scope is a convenient
way to drive the board from a notebook, and ``TraceSet`` opens a campaign
captured on another machine.

See ``hostlab/README.md`` for the measurement setup and the recommended
order of experiments.
"""

from .analysis import THRESHOLD, assess, summary, welch_t
from .campaign import Campaign, CampaignError
from .protocol import Link, ProtocolError
from .scope import NullScope, Scope, VisaScope
from .storage import TraceSet
from .target import Outcome, Target
from .vectors import VectorSet, generate, load

__version__ = "0.1.0"

__all__ = [
    "Campaign",
    "CampaignError",
    "Link",
    "NullScope",
    "Outcome",
    "ProtocolError",
    "Scope",
    "THRESHOLD",
    "Target",
    "TraceSet",
    "VectorSet",
    "VisaScope",
    "assess",
    "generate",
    "load",
    "summary",
    "welch_t",
]

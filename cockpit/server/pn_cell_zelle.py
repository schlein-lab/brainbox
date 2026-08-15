#!/usr/bin/env python3

from __future__ import annotations

from pn_cell_lifecycle import CellLifecycleMixin
from pn_cell_broker import CellBrokerNetzMixin
from pn_cell_fern import CellFernMixin
from pn_cell_laufzeiten import CellLaufzeitenMixin
from pn_cell_transcript import CellTranscriptMixin

class CellSession(CellLifecycleMixin, CellBrokerNetzMixin, CellFernMixin,
                  CellLaufzeitenMixin, CellTranscriptMixin):
    pass

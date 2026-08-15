#!/usr/bin/env python3

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

try:
    import pn_session_cells as _sc
except Exception:
    _sc = None

try:
    import pn_ram_admission as _ADMIT
except Exception:
    _ADMIT = None

_pfad = os.path.dirname(os.path.abspath(__file__))
if _pfad not in sys.path:
    sys.path.insert(0, _pfad)
del _pfad

from pn_cell_basis import (
    ACTD,
    ADOPT_WAIT_S,
    AGENTS_CA_GUEST,
    AGENTS_GEMINI_GUEST,
    AGENTS_LIB_GUEST,
    AGENTS_NODE_GUEST,
    AGENTS_OPENCODE_GUEST,
    AGENTS_RT_DIR,
    AGENTS_RT_IMG,
    BASE,
    BIN,
    BIOMNI_ENTRY_SRC,
    BIOMNI_LAKE_IMG,
    BIOMNI_RT_DIR,
    BIOMNI_RT_IMG,
    BOOT_TRIES,
    BROKER,
    CELLFS_SRC,
    CLOCK_DRIFT_MAX_S,
    CLOCK_SYNC_S,
    CODEX_BIN_GUEST,
    CODEX_CA_GUEST,
    CODEX_PATH_DIR_GUEST,
    CODEX_RT_DIR,
    CODEX_RT_IMG,
    DESK_BRIDGE,
    EXCHANGE_SRC,
    IDLE_STOP_S,
    INITRD,
    KALT_REPL_WARTEN,
    KALT_STILL_HART,
    KERNEL,
    LLMPOOL_CFG,
    LLMPOOL_STATE,
    MEM_MB,
    NET_BROKER,
    OFFICE_BASE,
    OFFICE_MEM_MB,
    OFFICE_VCPUS,
    PNJOB_SRC,
    PN_VMM_HOME,
    PORTALCTL_SRC,
    PORTAL_BROKER,
    READOPT_ON,
    READY_WAIT_S,
    REMOTE_READOPT_WAIT_S,
    RUN_DIR,
    SEAT_WAIT_S,
    SONOS_SRC,
    TERM_INCELL_SRC,
    TERM_RELAUNCH_MAX,
    TERM_RELAUNCH_WINDOW_S,
    TERM_START_WAIT_S,
    VOICE_MAX_TURNS,
    VOL_DIR,
    WORK_GB,
    _ADMIT,
    _AdoptedProc,
    _INJECTED_USER_MARKERS,
    _SHA_CACHE,
    _SHA_CACHE_LOCK,
    _VOICE_META_ARTIFACTS,
    _WEB_TOOLS,
    _cell_name,
    _cli_disallowed,
    _file_sha256,
    _is_injected_user_text,
    _read_proc_stat,
    _render_prompt_tool,
    _sc,
    _sock_lebt,
    _speakable,
    _split_sentences,
    _stream_text,
    _whoami,
    cells_enabled,
    llm_lane_reason,
    preflight,
)
from pn_cell_gastquellen import (
    CLAUDE_LAUNCH,
    CLAUDE_LAUNCH_TMPL,
    REMOTE_CLAUDE_LAUNCH_TMPL,
    _DNS_STUB_SRC,
    _VPN_SSH_SRC,
    _sonos_rooms_b64,
)
from pn_cell_volumes import (
    _delta_gesund_machen,
    _delta_want_mb,
    _delta_zustand,
    _kill_delta_orphans,
    _prep_delta,
    _prep_work,
)
from pn_cell_broker import (
    _INTERACTIVE_CG,
    _cell_broker_pids,
    _kill_cell_brokers,
    _pnd_rpc,
    _reap_dead_cell_brokers,
)
from pn_cell_fern import (
    RemoteVmm,
    _NODE_CELLS_NOLIST_BACKOFF_S,
    _NODE_CELLS_TIMEOUT_S,
    _NODE_CELLS_TTL_S,
    _node_cells_fetch,
    _node_cells_get,
    _node_status,
    _node_status_entry,
    _node_status_lock,
    _node_status_loop,
    _node_status_refresh,
    _node_status_thread,
    _node_status_thread_start,
    fern_zellen_uebersicht,
    node_status_info,
)
from pn_cell_lifecycle import (
    _SECRET_PROVIDER,
    set_secret_provider,
)
from pn_cell_zelle import (
    CellSession,
)
from pn_cell_manager import (
    CellManager,
    _MANAGER,
    _MGR_LOCK,
    _portaltest,
    _selftest,
    get_manager,
)

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    if "--portaltest" in sys.argv:
        raise SystemExit(_portaltest())
    print("pn_cell_session — import me; --selftest / --portaltest to verify.")

"""Run the API on the existing tunnel port and record its owned process ID."""
import argparse
import json
import os
import socket
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from crm.config import Settings
from crm.api import create_app
from crm.storage import atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    cfg = Settings.load()
    # Check the port before changing the recorded owner of a running service.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", args.port))
    executable = sys.executable
    if os.name == "nt":
        import ctypes
        module_path = ctypes.create_unicode_buffer(32768)
        get_module = ctypes.windll.kernel32.GetModuleFileNameW
        get_module.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
        get_module.restype = ctypes.c_uint
        if not get_module(None, module_path, len(module_path)):
            raise OSError("Cannot identify API process executable")
        executable = module_path.value
    state_path = cfg.data_dir / "runtime/https-processes.json"
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    state.update(api_pid=os.getpid(), api_executable=executable, api_parent_pid=os.getppid(), api_launcher=sys.executable, port=args.port,
                 processing_enabled=cfg.live_enabled)
    atomic_json(state_path, state)
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()

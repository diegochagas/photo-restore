#!/usr/bin/env python3
"""Side-by-side review of restore results: original | restored, a pick per
photo (original / restored / redo) and a note, saved to
<results>/preferences.json so the agent can read them later.

    compare_server.py [--results DIR] [--originals DIR]... [--port N]

Settings (arguments win, else ~/.config/photo-restore/compare.env):
  COMPARE_RESULTS    the run's output folder (the one restore.py wrote to,
                     e.g. <root>/<folder name>); required
  COMPARE_ORIGINALS  where the scans are - one folder or several separated
                     by ':' (searched recursively, matched by file name
                     without extension); may be left empty when every
                     results folder has an originals/ copy
  COMPARE_PORT       default 8790

A results folder is one set when it holds restored/ itself, and one set per
sub-folder that holds restored/ (tiers, re-run rounds "Round N" - newest
first). A set's own originals/ folder wins over COMPARE_ORIGINALS.
Optional <results>/manifest.csv (columns file, issues, optionally tier) adds
a line of context per photo. Listens on 127.0.0.1 only.
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path.home() / ".config/photo-restore/compare.env"
EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def load_config():
    cfg = {}
    if CONFIG.exists():
        for line in CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip("'\"")
                if v:
                    cfg[k.strip()] = v
    return cfg


def round_key(name):
    """'Round 10' before 'Round 9 seed 23' before 'Round 2'; other sets after, by name."""
    m = re.match(r"Round (\d+)(.*)", name)
    return (0, -int(m.group(1)), m.group(2)) if m else (1, 0, name)


def images(folder, recursive=False):
    it = folder.rglob("*") if recursive else folder.iterdir()
    return [p for p in it if p.is_file() and p.suffix.lower() in EXT]


class Library:
    def __init__(self, results, originals):
        self.results = results
        self.originals = originals
        self.prefs_path = results / "preferences.json"
        self.files = {}          # url id -> real path, the only files served

    def sets(self):
        found = []
        if (self.results / "restored").is_dir():
            found.append((self.results.name, self.results))
        for d in self.results.iterdir():
            if d.is_dir() and (d / "restored").is_dir():
                found.append((d.name, d))
        return sorted(found, key=lambda s: round_key(s[0]))

    def load_prefs(self):
        try:
            return json.loads(self.prefs_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_prefs(self, data):
        tmp = self.prefs_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.prefs_path)

    def manifest(self):
        info = {}
        m = self.results / "manifest.csv"
        if m.exists():
            with m.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("file"):
                        info[(row.get("tier", ""), row["file"])] = row.get("issues", "")
                        info.setdefault(("", Path(row["file"]).stem), row.get("issues", ""))
        return info

    def photos(self):
        shared = {}
        for folder in self.originals:
            for p in images(folder, recursive=True):
                shared.setdefault(p.stem, p)
        info, prefs = self.manifest(), self.load_prefs()
        self.files = {}
        out = []
        sets = self.sets()
        rank = {name: i for i, (name, _) in enumerate(sets)}   # newest first
        for name, d in sets:
            own = {p.stem: p for p in images(d / "originals")} if (d / "originals").is_dir() else {}
            for r in sorted(images(d / "restored"), key=lambda p: p.name):
                o = own.get(r.stem) or shared.get(r.stem)
                if not o:
                    continue
                pid = f"{name}/{o.name}"
                k = len(self.files)
                self.files[f"o{k}"], self.files[f"r{k}"] = o, r
                line = info.get((name, o.name)) or info.get(("", r.stem), "")
                # your latest note on the same photo in an older set (an earlier round)
                older = [(v.get("at", ""), key.split("/", 1)[0], v["note"]) for key, v in prefs.items()
                         if key.split("/", 1)[-1] == o.name and v.get("note")
                         and rank.get(key.split("/", 1)[0], -1) > rank[name]]
                if older:
                    _, where, note = max(older)
                    line = (line + " — " if line else "") + f"your note ({where}): {note}"
                out.append({"id": pid, "tier": name, "name": r.stem, "issues": line,
                            "original": f"img/o{k}", "restored": f"img/r{k}"})
        return out


def make_handler(lib):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(HERE), **kw)

        def send_json(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/compare.html"):
                self.path = "/compare.html"
                return super().do_GET()
            if path == "/api/photos":
                return self.send_json(lib.photos())
            if path == "/api/prefs":
                return self.send_json(lib.load_prefs())
            if path.startswith("/img/"):
                f = lib.files.get(path[5:])
                if not f:
                    return self.send_error(404)
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", self.guess_type(str(f)))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404)

        def do_POST(self):
            if self.path != "/api/prefs":
                return self.send_error(404)
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                assert isinstance(data, dict)
            except Exception:
                return self.send_error(400)
            lib.save_prefs(data)
            self.send_json({"ok": True, "count": len(data)})

        def log_message(self, fmt, *args):
            if args and "/api/prefs" in str(args[0]) and "POST" in str(args[0]):
                super().log_message(fmt, *args)

    return Handler


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=cfg.get("COMPARE_RESULTS"))
    ap.add_argument("--originals", action="append",
                    help="folder with the scans (repeatable); default COMPARE_ORIGINALS")
    ap.add_argument("--port", type=int, default=int(cfg.get("COMPARE_PORT", 8790)))
    a = ap.parse_args()
    if not a.results:
        sys.exit(f"no results folder: pass --results or set COMPARE_RESULTS in {CONFIG}")
    results = Path(os.path.expanduser(a.results)).resolve()
    if not results.is_dir():
        sys.exit(f"results folder not found: {results}")
    orig_arg = a.originals or [p for p in cfg.get("COMPARE_ORIGINALS", "").split(":") if p]
    originals = [Path(os.path.expanduser(p)).resolve() for p in orig_arg]
    missing = [str(p) for p in originals if not p.is_dir()]
    if missing:
        sys.exit(f"originals folder not found: {', '.join(missing)}")
    lib = Library(results, originals)
    if not lib.sets():
        sys.exit(f"nothing to compare: no restored/ folder in {results} or its sub-folders")
    n = len(lib.photos())
    if not n:
        sys.exit("no restored photo has a matching original - set COMPARE_ORIGINALS / --originals")
    print(f"Photo compare: http://localhost:{a.port}  ({n} photos, picks -> {lib.prefs_path})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(lib)).serve_forever()


if __name__ == "__main__":
    main()

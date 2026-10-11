#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qq 指令集共用模組：名稱搜尋、內容搜尋、curses 選單（Linux / macOS / Windows）。

選單畫在終端機（Unix: /dev/tty；Windows: CONOUT$），選取結果寫到 stdout，
或寫到環境變數 QQ_OUT 指定的檔案（Windows 殼層用）。
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
import zlib

try:
    import curses
except ImportError:  # Windows 沒裝 windows-curses
    curses = None  # type: ignore[assignment]

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
FS_ERRORS = "surrogatepass" if IS_WIN else "surrogateescape"

if IS_MAC:
    EXCLUDE_DIRS = ("/dev", "/System/Volumes", "/Volumes", "/private/var/vm", "/private/var/folders")
elif IS_WIN:
    EXCLUDE_DIRS = ()
else:
    EXCLUDE_DIRS = ("/proc", "/sys", "/dev", "/run")

MAX_RESULTS = 20000
MAX_GREP_RESULTS = 5000

BRE_SPECIAL = set(".^$*[]\\")
RUST_SPECIAL = set(".+*?()[]{}|^$\\")

# 一般登入 shell 的 PATH 不一定有 fd / rg
EXTRA_BIN_DIRS = tuple(
    d for d in (
        os.path.expanduser("~/.pi/agent/bin"),
        os.path.expanduser("~/.local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links") if IS_WIN else "",
        os.path.expanduser(r"~\scoop\shims") if IS_WIN else "",
    ) if d
)


def cache_dir() -> str:
    """索引放置處：Windows %LOCALAPPDATA%\\qq；其他 ~/.cache/qq。"""
    env = os.environ.get("QQ_CACHE_DIR")
    if env:
        return env
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "qq")
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "qq")


class QQError(Exception):
    """可預期錯誤（會以簡短訊息回報使用者）。"""


class Options:
    __slots__ = ("mode", "fresh", "query", "help")

    def __init__(self, mode: str, fresh: bool, query: str | None, help_: bool):
        self.mode = mode
        self.fresh = fresh
        self.query = query
        self.help = help_


class GrepOptions:
    __slots__ = ("pattern", "root", "regex", "help")

    def __init__(self, pattern: str | None, root: str | None, regex: bool, help_: bool):
        self.pattern = pattern
        self.root = root
        self.regex = regex
        self.help = help_


def find_bin(*names: str) -> str | None:
    """在 PATH 與常見額外目錄中找可執行檔。"""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
        for directory in EXTRA_BIN_DIRS:
            for ext in ((".exe", "") if IS_WIN else ("",)):
                candidate = os.path.join(directory, name + ext)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
    return None


# --------------------------------------------------------------------------
# 名稱搜尋參數
# --------------------------------------------------------------------------
def parse_name_args(argv: list[str], cmd: str) -> Options:
    mode = "both"
    fresh = False
    help_ = False
    positional: list[str] = []
    only_positional = False

    for arg in argv:
        if only_positional:
            positional.append(arg)
            continue
        if arg == "--":
            only_positional = True
        elif arg in ("-d", "--dir", "--directory"):
            mode = "dir"
        elif arg in ("-f", "--file"):
            mode = "file"
        elif arg == "--fresh":
            fresh = True
        elif arg in ("-h", "--help"):
            help_ = True
        elif arg.startswith("-") and arg != "-":
            raise QQError(
                f"未知的選項「{arg}」（可用 -d、-f、--fresh，詳見 qq {cmd} --help）"
            )
        else:
            positional.append(arg)

    if help_:
        return Options(mode, fresh, None, True)
    if not positional:
        raise QQError(f"請提供要搜尋的名稱，例如：qq {cmd} -d mnt")
    if len(positional) > 1:
        raise QQError("一次只能搜尋一個名稱（名稱含空白時請用引號包起來）")
    return Options(mode, fresh, positional[0], False)


# --------------------------------------------------------------------------
# 搜尋：索引
#   Linux  ：plocate（有裝就用）→ 否則 qq 自建索引
#   macOS  ：Spotlight（mdfind）＋ BSD locate 自建庫，合併
#   Windows：qq 自建索引（Python 掃全部固定磁碟，qq-updatedb 建、排程每天更新）
# --------------------------------------------------------------------------
LOCATE_DB = os.environ.get("QQ_LOCATE_DB", os.path.join(cache_dir(), "locate.database"))
OWN_INDEX = os.path.join(cache_dir(), "index.gz")


def index_name() -> str:
    if IS_MAC:
        return "Spotlight＋locate 索引"
    if IS_WIN:
        return "qq 索引"
    return "plocate 索引" if find_bin("plocate") else "qq 索引"


def bre_escape(text: str) -> str:
    return "".join(("\\" + c) if c in BRE_SPECIAL else c for c in text)


def search_index(query: str) -> list[str]:
    if IS_MAC:
        return search_macos_index(query)
    if IS_WIN:
        return search_own_index(query)
    if os.environ.get("QQ_INDEX") != "own" and find_bin("plocate"):
        return search_plocate(query)
    return search_own_index(query)


# ---- qq 自建索引（Windows、沒有 plocate 的 Linux） ----------------------
# 格式：gzip 文字檔，每行一條完整路徑（UTF-8，Windows 用 surrogatepass）。
# 第一行是標頭 "#qq-index v1 <epoch>"。查詢時逐行比對最後一段名稱（不分大小寫）。
INDEX_HEADER = "#qq-index v1"


def search_own_index(query: str) -> list[str]:
    """分塊解壓＋正規表示式找「分隔符號＋名稱＋換行」，比逐行 Python 迴圈快很多。"""
    import re
    if not os.path.isfile(OWN_INDEX):
        raise QQError(
            f"還沒有索引（{OWN_INDEX}）。執行 qq-updatedb 建立（約數分鐘），或先用 --fresh 即時掃描"
        )
    seps = r"[\\/]" if IS_WIN else "/"
    rx = re.compile(seps + re.escape(query) + r"(?=\n)", re.IGNORECASE)
    out: list[str] = []
    rest = ""
    first = True
    try:
        with gzip.open(OWN_INDEX, "rb") as fh:
            while True:
                chunk = fh.read(8 * 1024 * 1024)
                if not chunk and not rest:
                    break
                text = rest + chunk.decode("utf-8", FS_ERRORS)
                if chunk:
                    cut = text.rfind("\n") + 1
                    text, rest = text[:cut], text[cut:]
                else:
                    rest = ""
                    if not text.endswith("\n"):
                        text += "\n"
                if first:
                    if not text.startswith(INDEX_HEADER):
                        raise QQError(f"索引格式不符：{OWN_INDEX}（重跑 qq-updatedb）")
                    first = False
                for m in rx.finditer(text):
                    ls = text.rfind("\n", 0, m.start()) + 1
                    out.append(text[ls:m.end()])
                    if len(out) >= MAX_RESULTS:
                        return out
                if not chunk:
                    break
    except (OSError, EOFError, zlib.error) as exc:
        raise QQError(f"索引讀取失敗：{exc}（重跑 qq-updatedb）")
    return out


def default_index_roots() -> list[str]:
    if IS_WIN:
        roots = []
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i in range(26):
                if mask & (1 << i):
                    root = f"{chr(65 + i)}:\\"
                    # 3 = DRIVE_FIXED，只收固定磁碟（略過光碟、網路、隨身碟）
                    if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                        roots.append(root)
        except Exception:
            roots = [os.environ.get("SystemDrive", "C:") + "\\"]
        return roots
    return ["/"]


WIN_SKIP = {
    "$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws",
    "$sysreset", "$winreagent", "config.msi",
}
WIN_SKIP_PATHS = ("\\windows\\winsxs", "\\windows\\servicing", "\\windows\\installer",
                  "\\windows\\softwaredistribution", "\\windows\\systemtemp")
UNIX_SKIP_PATHS = ("/proc", "/sys", "/dev", "/run", "/tmp", "/var/tmp", "/var/cache",
                   "/snap", "/var/lib/docker", "/var/lib/containers", "/mnt", "/media",
                   "/var/lib/plocate", "/var/lib/mlocate")


def walk_paths(roots: list[str]):
    """以 os.scandir 走訪，不跟隨符號連結／junction；Unix 略過虛擬與暫存目錄、/mnt、/media。"""
    for root in roots:
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    path = e.path
                    yield path
                    try:
                        if not e.is_dir(follow_symlinks=False):
                            continue
                        if IS_WIN:
                            st = e.stat(follow_symlinks=False)
                            # 0x400 = FILE_ATTRIBUTE_REPARSE_POINT（junction、OneDrive 雲端占位等）
                            if st.st_file_attributes & 0x400:
                                continue
                            low = path.lower()
                            if e.name.lower() in WIN_SKIP or low[2:] in WIN_SKIP_PATHS:
                                continue
                        elif path in UNIX_SKIP_PATHS:
                            continue
                    except OSError:
                        continue
                    stack.append(path)


def build_own_index(roots: list[str] | None = None) -> tuple[int, str]:
    roots = roots or default_index_roots()
    os.makedirs(cache_dir(), exist_ok=True)
    tmp = OWN_INDEX + f".tmp{os.getpid()}"
    n = 0
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", errors=FS_ERRORS, newline="\n", compresslevel=3) as fh:
            fh.write(f"{INDEX_HEADER} {int(time.time())} {' '.join(roots)}\n")
            for path in walk_paths(roots):
                if "\n" in path:
                    continue
                fh.write(path + "\n")
                n += 1
        os.replace(tmp, OWN_INDEX)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return n, OWN_INDEX


# ---- macOS ---------------------------------------------------------------
def search_macos_index(query: str) -> list[str]:
    """macOS：Spotlight（mdfind）+ BSD locate 自建庫，兩邊合併。

    * Spotlight 系統即時維護，涵蓋 Desktop/Documents/Downloads/iCloud/Dropbox，
      但不收隱藏目錄（例如 ~/.codex）。
    * locate 庫由 qq-updatedb 每天在背景重建，涵蓋隱藏目錄與系統路徑，
      但背景程式受 macOS 隱私保護（TCC），看不到上述個人資料夾。
    """
    results: list[str] = []
    errors: list[str] = []
    for fn in (search_spotlight, search_locate):
        try:
            results.extend(fn(query))
        except QQError as exc:
            errors.append(str(exc))
    if not results and len(errors) == 2:
        raise QQError("；".join(errors))
    return results


def search_spotlight(query: str) -> list[str]:
    mdfind = "/usr/bin/mdfind"
    if not os.path.exists(mdfind):
        raise QQError("找不到 mdfind")
    esc = "".join(("\\" + c) if c in '"\\*' else c for c in query)
    cmd = [mdfind, "-0", f'kMDItemFSName == "{esc}"c']
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise QQError("Spotlight 查詢逾時")
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise QQError(f"Spotlight 查詢失敗：{err[-1] if err else proc.returncode}")
    raw = proc.stdout.decode("utf-8", "surrogateescape")
    return [p for p in raw.split("\0") if p][:MAX_RESULTS]


def search_locate(query: str) -> list[str]:
    if not os.path.isfile(LOCATE_DB):
        raise QQError(f"找不到 locate 資料庫 {LOCATE_DB}（執行 qq-updatedb 可建立）")
    glob = "".join(("\\" + c) if c in "*?[]\\" else c for c in query)
    cmd = ["/usr/bin/locate", "-i", "-0", "-l", str(MAX_RESULTS), "-d", LOCATE_DB, "*/" + glob]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise QQError(f"locate 執行失敗：{err or proc.returncode}")
    raw = proc.stdout.decode("utf-8", "surrogateescape")
    return [p for p in raw.split("\0") if p]


# ---- Linux plocate -------------------------------------------------------
def search_plocate(query: str) -> list[str]:
    pattern = "^" + bre_escape(query) + "$"
    cmd = ["plocate", "-b", "-i", "-r", "-0", "-l", str(MAX_RESULTS), "--", pattern]
    proc = subprocess.run(cmd, capture_output=True)
    err = proc.stderr.decode("utf-8", "replace").strip()
    if proc.returncode not in (0, 1):
        raise QQError(f"plocate 執行失敗：{err or proc.returncode}")
    if proc.returncode == 1 and err:
        raise QQError(f"plocate 資料庫無法使用：{err}（可用 --fresh 即時掃描）")
    raw = proc.stdout.decode("utf-8", "surrogateescape")
    return [p for p in raw.split("\0") if p]


# --------------------------------------------------------------------------
# 搜尋：--fresh 即時掃描（優先 fd；沒有就 find / Python 走訪）
# --------------------------------------------------------------------------
def rust_escape(text: str) -> str:
    return "".join(("\\" + c) if c in RUST_SPECIAL else c for c in text)


def search_fresh(query: str) -> list[str]:
    fd = find_bin("fd", "fdfind")
    if fd:
        return _search_with_fd(fd, query)
    if IS_WIN:
        return _search_with_walk(query)
    return _search_with_find(query)


def _search_with_walk(query: str) -> list[str]:
    want = query.casefold()
    out: list[str] = []
    for path in walk_paths(default_index_roots()):
        if os.path.basename(path).casefold() == want:
            out.append(path)
            if len(out) >= MAX_RESULTS:
                break
    return out


def _search_with_fd(fd: str, query: str) -> list[str]:
    cmd = [
        fd,
        "--absolute-path",
        "--hidden",
        "--no-ignore",
        "--color", "never",
        "--print0",
    ]
    for d in EXCLUDE_DIRS:
        cmd += ["--exclude", d]
    cmd += ["--", "^" + rust_escape(query) + "$"]
    cmd += default_index_roots()
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode not in (0, 1):
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise QQError(f"fd 即時掃描失敗：{err or proc.returncode}")
    raw = proc.stdout.decode("utf-8", FS_ERRORS)
    return [p for p in raw.split("\0") if p][:MAX_RESULTS]


def _search_with_find(query: str) -> list[str]:
    glob = "".join(("\\" + c) if c in "*?[\\" else c for c in query)
    expr: list[str] = []
    for i, d in enumerate(EXCLUDE_DIRS):
        if i:
            expr.append("-o")
        expr += ["-path", d]
    cmd = ["find", "/", "("] + expr + [")", "-prune", "-o", "-iname", glob, "-print0"]
    proc = subprocess.run(cmd, capture_output=True)
    raw = proc.stdout.decode("utf-8", "surrogateescape")
    return [p for p in raw.split("\0") if p][:MAX_RESULTS]


# --------------------------------------------------------------------------
# 結果整理
# --------------------------------------------------------------------------
def norm_path(path: str) -> str:
    if IS_WIN:
        path = os.path.normpath(path)
        if len(path) > 3:
            path = path.rstrip("\\")
        return path
    return path.rstrip("/") or "/"


def collect(paths: list[str], mode: str) -> list[tuple[str, bool]]:
    """過濾失效路徑、分類並排序，回傳 [(完整路徑, 是否為資料夾), ...]。"""
    dirs: list[str] = []
    files: list[str] = []
    seen: set[str] = set()
    for path in paths:
        path = norm_path(path)
        if path in seen:
            continue
        seen.add(path)
        try:
            if os.path.isdir(path):
                dirs.append(path)
            elif os.path.isfile(path):
                files.append(path)
        except OSError:
            continue

    def key(p: str):
        return (p.count(os.sep), p.lower())

    dirs.sort(key=key)
    files.sort(key=key)

    if mode == "dir":
        chosen = dirs
    elif mode == "file":
        chosen = files
    else:
        chosen = dirs + files

    dir_set = set(dirs)
    return [(p, p in dir_set) for p in chosen]


# --------------------------------------------------------------------------
# 內容搜尋（grep）
# --------------------------------------------------------------------------
def parse_grep_args(argv: list[str]) -> GrepOptions:
    regex = False
    help_ = False
    positional: list[str] = []
    only_positional = False

    for arg in argv:
        if only_positional:
            positional.append(arg)
            continue
        if arg == "--":
            only_positional = True
        elif arg in ("-h", "--help"):
            help_ = True
        elif arg == "--regex":
            regex = True
        elif arg.startswith("-") and arg != "-":
            raise QQError(
                f"未知的選項「{arg}」（可用 --regex，詳見 qq grep --help）"
            )
        else:
            positional.append(arg)

    if help_:
        return GrepOptions(None, None, regex, True)
    if not positional:
        raise QQError("請提供要搜尋的文字，例如：qq grep TODO")
    if len(positional) > 2:
        raise QQError("用法：qq grep <文字> [目錄]")
    pattern = positional[0]
    if pattern == "":
        raise QQError("搜尋文字不能是空的")
    root = os.path.abspath(positional[1]) if len(positional) == 2 else os.getcwd()
    if not os.path.isdir(root):
        raise QQError(f"目錄不存在：「{root}」")
    return GrepOptions(pattern, root, regex, False)


def search_content(pattern: str, root: str, regex: bool) -> list[tuple[str, str]]:
    """回傳 [(完整路徑, 內容摘要), ...]。優先 rg，沒有則用 GNU grep。"""
    rg = find_bin("rg")
    if rg:
        return _search_with_rg(rg, pattern, root, regex)
    if IS_WIN or not find_bin("grep"):
        return _search_with_python(pattern, root, regex)
    return _search_with_grep(pattern, root, regex)


def _search_with_python(pattern: str, root: str, regex: bool) -> list[tuple[str, str]]:
    """沒有 rg / grep 時的純 Python 後備（較慢）。"""
    import re
    try:
        rx = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE)
    except re.error as exc:
        raise QQError(f"正規表示式錯誤：{exc}")
    results: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "rb") as fh:
                    head = fh.read(8192)
                    if b"\0" in head:
                        continue
                    data = head + fh.read(4 * 1024 * 1024)
            except OSError:
                continue
            text = data.decode("utf-8", "replace")
            m = rx.search(text)
            if not m:
                continue
            lineno = text.count("\n", 0, m.start()) + 1
            ls = text.rfind("\n", 0, m.start()) + 1
            le = text.find("\n", m.start())
            line = text[ls: le if le != -1 else len(text)].strip()
            results.append((path, f"{lineno}: {line}"))
            if len(results) >= MAX_GREP_RESULTS:
                return results
    return results


def _search_with_rg(rg: str, pattern: str, root: str, regex: bool) -> list[tuple[str, str]]:
    cmd = [
        rg,
        "-i",
        "--json",
        "--max-count", "1",
        "--hidden",
        "--color", "never",
    ]
    if not regex:
        cmd.append("-F")
    for name in ("proc", "sys", "dev", "run", ".git"):
        cmd += ["--glob", f"!{name}/**"]
    cmd += ["--", pattern, root]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode not in (0, 1):
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise QQError(f"內容搜尋失敗：{err or proc.returncode}")

    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in proc.stdout.splitlines():
        if len(results) >= MAX_GREP_RESULTS:
            break
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data") or {}
        path_obj = data.get("path") or {}
        path = path_obj.get("text") or path_obj.get("absolute")
        if not path:
            continue
        path = os.path.abspath(path)
        if path in seen:
            continue
        seen.add(path)
        text = (data.get("lines") or {}).get("text") or ""
        text = text.replace("\n", " ").replace("\r", "").strip()
        lineno = data.get("line_number")
        snippet = f"{lineno}: {text}" if lineno else text
        results.append((path, snippet))
    return results


def _search_with_grep(pattern: str, root: str, regex: bool) -> list[tuple[str, str]]:
    cmd = [
        "grep", "-rIl", "-i",
        "--binary-files=without-match",
        "--exclude-dir=proc",
        "--exclude-dir=sys",
        "--exclude-dir=dev",
        "--exclude-dir=run",
        "--exclude-dir=.git",
    ]
    if not regex:
        cmd.append("-F")
    cmd += ["--", pattern, root]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode not in (0, 1):
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise QQError(f"內容搜尋失敗：{err or proc.returncode}")
    raw = proc.stdout.decode("utf-8", "surrogateescape")
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        if len(results) >= MAX_GREP_RESULTS:
            break
        path = os.path.abspath(line.strip())
        if not path or path in seen:
            continue
        seen.add(path)
        results.append((path, ""))
    return results


def collect_grep(hits: list[tuple[str, str]]) -> tuple[list[tuple[str, bool]], list[str]]:
    """過濾失效檔案並排序，回傳 (items, snippets)。"""
    files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path, snippet in hits:
        path = norm_path(path)
        if path in seen:
            continue
        seen.add(path)
        try:
            if os.path.isfile(path):
                files.append((path, snippet))
        except OSError:
            continue

    files.sort(key=lambda t: (t[0].count(os.sep), t[0].lower()))
    items = [(p, False) for p, _ in files]
    snippets = [s for _, s in files]
    return items, snippets


# --------------------------------------------------------------------------
# 互動介面
# --------------------------------------------------------------------------
def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text: str) -> int:
    return sum(char_width(c) for c in text)


def put(stdscr, y: int, x: int, text: str, max_w: int, attr: int = 0) -> int:
    """在 (y, x) 輸出文字，依顯示寬度截斷，回傳新的 x。"""
    if max_w <= 0:
        return x
    kept: list[str] = []
    width = 0
    for ch in text:
        cw = char_width(ch)
        if width + cw > max_w:
            break
        kept.append(ch)
        width += cw
    try:
        stdscr.addstr(y, x, "".join(kept), attr)
    except curses.error:
        pass
    return x + width


def fit_right(text: str, max_w: int) -> str:
    """截斷到指定顯示寬度，保留右端（用於檔名），左側加省略號。"""
    if max_w <= 0:
        return ""
    if text_width(text) <= max_w:
        return text
    kept: list[str] = []
    width = 0
    for ch in reversed(text):
        cw = char_width(ch)
        if width + cw > max_w - 1:
            break
        kept.append(ch)
        width += cw
    return "…" + "".join(reversed(kept))


def fit_left(text: str, max_w: int) -> str:
    """截斷到指定顯示寬度，保留左端，右側加省略號。"""
    if max_w <= 0:
        return ""
    if text_width(text) <= max_w:
        return text
    kept: list[str] = []
    width = 0
    for ch in text:
        cw = char_width(ch)
        if width + cw > max_w - 1:
            break
        kept.append(ch)
        width += cw
    return "".join(kept) + "…"


def draw_row(stdscr, y: int, item: tuple[str, bool], selected: bool, width: int, colors: bool) -> None:
    """繪製一列：`D`/`F` 標記 + 完整路徑。優先保留檔名。"""
    path, is_dir = item
    base_attr = curses.A_REVERSE if selected else 0

    head, tail = os.path.split(path)
    if not tail:  # 根目錄：/ 或 C:\\
        head, tail = "", path
    elif head and not head.endswith(os.sep):
        head += os.sep

    prefix = "D " if is_dir else "F "
    avail = max(0, width - len(prefix))
    tail_txt = fit_right(tail, avail)
    head_txt = fit_left(head, max(0, avail - text_width(tail_txt)))

    x = put(stdscr, y, 0, prefix, width, base_attr | curses.A_BOLD)
    x = put(stdscr, y, x, head_txt, width - x, base_attr)
    tail_attr = base_attr
    if colors and not selected:
        tail_attr |= curses.color_pair(1) | curses.A_BOLD
    put(stdscr, y, x, tail_txt, width - x, tail_attr)


def read_key(stdscr) -> int:
    """讀取按鍵，相容 ncurses 未辨識的 CSI/SS3 方向鍵序列。"""
    key = stdscr.getch()
    if key != 27:
        return key

    stdscr.timeout(30)
    seq = b""
    try:
        while len(seq) < 8:
            nxt = stdscr.getch()
            if nxt == -1:
                break
            seq += bytes((nxt,))
            if nxt in b"~ABCDHFZ":
                break
    finally:
        stdscr.timeout(-1)

    if not seq or seq.startswith(b"\x1b"):
        return 27

    mapping = {
        b"[A": curses.KEY_UP, b"OA": curses.KEY_UP,
        b"[B": curses.KEY_DOWN, b"OB": curses.KEY_DOWN,
        b"[C": curses.KEY_RIGHT, b"OC": curses.KEY_RIGHT,
        b"[D": curses.KEY_LEFT, b"OD": curses.KEY_LEFT,
        b"[H": curses.KEY_HOME, b"OH": curses.KEY_HOME,
        b"[1~": curses.KEY_HOME, b"[7~": curses.KEY_HOME,
        b"[F": curses.KEY_END, b"OF": curses.KEY_END,
        b"[4~": curses.KEY_END, b"[8~": curses.KEY_END,
        b"[5~": curses.KEY_PPAGE, b"[6~": curses.KEY_NPAGE,
    }
    return mapping.get(seq, -2)


def ui_loop(
    stdscr,
    items: list[tuple[str, bool]],
    title: str,
    enter_hint,
    snippets: list[str] | None,
) -> str | None:
    curses.curs_set(0)
    stdscr.keypad(True)
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass
    colors = False
    if curses.has_colors():
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            colors = True
        except curses.error:
            colors = False

    selected = 0
    top = 0
    has_snippet = bool(snippets) and any(snippets)

    while True:
        height, width = stdscr.getmaxyx()
        footer_lines = 4 if has_snippet and height >= 5 else 3
        list_h = max(1, height - footer_lines)
        if selected < top:
            top = selected
        if selected >= top + list_h:
            top = selected - list_h + 1

        stdscr.erase()
        put(stdscr, 0, 0, title, width, curses.A_BOLD)

        for row in range(list_h):
            idx = top + row
            if idx >= len(items):
                break
            draw_row(stdscr, 1 + row, items[idx], idx == selected, width, colors)

        path, is_dir = items[selected]
        action = enter_hint(path, is_dir)

        if has_snippet and height >= 5:
            snippet = snippets[selected] if snippets else ""
            x = put(stdscr, height - 3, 0, "內容：", width, curses.A_BOLD)
            put(stdscr, height - 3, x, fit_right(snippet, width - x), width - x)

        if height >= 3:
            y_sel = height - 2 if height >= 4 else height - 1
            if has_snippet and height >= 5:
                y_sel = height - 2
            x = put(stdscr, y_sel, 0, "選取：", width, curses.A_BOLD)
            put(stdscr, y_sel, x, fit_right(path, width - x), width - x, curses.A_BOLD)

        if height >= 4:
            hint = f"Enter 選擇 → {action}   ↑↓/jk 移動  PgUp/PgDn 翻頁  g/G 頭尾  q/Esc 取消"
            put(stdscr, height - 1, 0, hint, width)

        stdscr.refresh()

        key = read_key(stdscr)
        if key in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            selected = min(len(items) - 1, selected + 1)
        elif key == curses.KEY_PPAGE:
            selected = max(0, selected - list_h)
        elif key == curses.KEY_NPAGE:
            selected = min(len(items) - 1, selected + list_h)
        elif key in (curses.KEY_HOME, ord("g")):
            selected = 0
        elif key in (curses.KEY_END, ord("G")):
            selected = len(items) - 1
        elif key in (curses.KEY_ENTER, 10, 13):
            return items[selected][0]
        elif key in (27, ord("q"), 3):
            return None
        elif key == curses.KEY_RESIZE:
            continue


def run_ui(
    items: list[tuple[str, bool]],
    title: str,
    enter_hint,
    snippets: list[str] | None = None,
) -> str | None:
    """在終端機上跑互動介面，回傳選取的路徑或 None（取消）。"""
    if curses is None:
        raise QQError("缺少 curses 模組（Windows 請執行：python -m pip install --user windows-curses）")
    if IS_WIN:
        return _run_ui_windows(items, title, enter_hint, snippets)
    out_fd = os.dup(1)
    in_fd = os.dup(0)
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        os.close(out_fd)
        os.close(in_fd)
        raise QQError("無法開啟互動終端 /dev/tty，請直接在終端機中使用")
    try:
        os.dup2(tty_fd, 0)
        os.dup2(tty_fd, 1)
        return curses.wrapper(ui_loop, items, title, enter_hint, snippets)
    finally:
        os.dup2(out_fd, 1)
        os.dup2(in_fd, 0)
        os.close(out_fd)
        os.close(in_fd)
        os.close(tty_fd)


def _run_ui_windows(items, title, enter_hint, snippets):
    """PDCurses 用 GetStdHandle 取得輸入輸出；PowerShell 接收輸出時 stdout 是管線，
    所以暫時把標準輸入輸出 handle 換成 console（CONIN$／CONOUT$），畫完再換回來。"""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetStdHandle.restype = wintypes.HANDLE
    k32.GetStdHandle.argtypes = (wintypes.DWORD,)
    k32.SetStdHandle.restype = wintypes.BOOL
    k32.SetStdHandle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
    STD_IN, STD_OUT = wintypes.DWORD(-10 & 0xFFFFFFFF), wintypes.DWORD(-11 & 0xFFFFFFFF)

    try:
        fo = os.open("CONOUT$", os.O_RDWR)
        fi = os.open("CONIN$", os.O_RDWR)
    except OSError:
        raise QQError("無法開啟主控台（CONIN$/CONOUT$），請直接在終端機中使用")
    old_out, old_in = k32.GetStdHandle(STD_OUT), k32.GetStdHandle(STD_IN)
    k32.SetStdHandle(STD_OUT, msvcrt.get_osfhandle(fo))
    k32.SetStdHandle(STD_IN, msvcrt.get_osfhandle(fi))
    try:
        return curses.wrapper(ui_loop, items, title, enter_hint, snippets)
    finally:
        k32.SetStdHandle(STD_OUT, old_out)
        k32.SetStdHandle(STD_IN, old_in)
        os.close(fo)
        os.close(fi)


def has_tty() -> bool:
    if os.environ.get("QQ_NO_TTY"):
        return False
    if IS_WIN:
        # 有 console 才能畫選單；ssh 非互動、排程工作等情況沒有
        try:
            fd = os.open("CONIN$", os.O_RDWR)
        except OSError:
            return False
        try:
            return os.isatty(fd)
        finally:
            os.close(fd)
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return False
    os.close(fd)
    return True


def choose_path(
    items: list[tuple[str, bool]],
    *,
    cmd: str,
    title: str,
    enter_hint,
    snippets: list[str] | None = None,
) -> str | None:
    """有終端機就顯示選單；否則取第一筆。取消回傳 None。"""
    if not has_tty():
        print(f"{cmd}: 無互動終端機，直接選取 {items[0][0]}", file=sys.stderr)
        return items[0][0]
    return run_ui(items, title, enter_hint, snippets)


def emit(path: str) -> None:
    """輸出選取結果。設了 QQ_OUT 就寫檔（Windows 殼層用，避開 console 編碼），否則寫 stdout。"""
    out = os.environ.get("QQ_OUT")
    if out:
        with open(out, "w", encoding="utf-8", errors=FS_ERRORS) as fh:
            fh.write(path)
        return
    data = (path + "\n").encode("utf-8", FS_ERRORS)
    try:
        sys.stdout.flush()
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
    except AttributeError:
        sys.stdout.write(path + "\n")


def hint_cd(path: str, is_dir: bool) -> str:
    dest = path if is_dir else (os.path.dirname(path) or path)
    return f"cd {dest}"


def hint_print(path: str, is_dir: bool) -> str:
    return f"輸出 {path}"


def run_name_command(cmd: str, argv: list[str], help_text: str) -> int:
    """qq find / qq pick 共用進入點。選取的路徑寫到 stdout。"""
    try:
        opts = parse_name_args(argv, cmd)
    except QQError as exc:
        print(f"qq {cmd}: {exc}", file=sys.stderr)
        return 2

    if opts.help:
        print(help_text, end="")
        return 0

    assert opts.query is not None
    try:
        paths = search_fresh(opts.query) if opts.fresh else search_index(opts.query)
    except QQError as exc:
        print(f"qq {cmd}: {exc}", file=sys.stderr)
        return 3

    items = collect(paths, opts.mode)
    if not items:
        what = {"dir": "資料夾", "file": "檔案", "both": "資料夾或檔案"}[opts.mode]
        source = "即時掃描" if opts.fresh else index_name()
        print(
            f"qq {cmd}: 在{source}中找不到名稱為「{opts.query}」的{what}",
            file=sys.stderr,
        )
        return 1

    mode_text = {"dir": "資料夾", "file": "檔案", "both": "資料夾+檔案"}[opts.mode]
    title = f"qq {cmd}  「{opts.query}」  {mode_text}  共 {len(items)} 筆"
    if opts.fresh:
        title += "  [--fresh 即時掃描]"
    enter_hint = hint_cd if cmd == "find" else hint_print

    try:
        chosen = choose_path(
            items, cmd=f"qq {cmd}", title=title, enter_hint=enter_hint
        )
    except QQError as exc:
        print(f"qq {cmd}: {exc}", file=sys.stderr)
        return 3

    if chosen is None:
        return 1
    emit(chosen)
    return 0

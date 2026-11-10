"""A list of filesystem operations that can be previewed (dry run), applied, and undone."""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)
Producer = Callable[[], bytes | None]


@dataclass
class Op:
    kind: str                 # mkdir | move | write | fetch | copy | rmdir_if_empty
    dst: Path
    src: Path | None = None
    producer: Producer | None = None
    note: str = ""
    overwrite: bool = False


@dataclass
class Plan:
    title: str
    ops: list[Op] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _gone: set[str] = field(default_factory=set)
    _new: set[str] = field(default_factory=set)
    _dir_moves: list[tuple[str, str]] = field(default_factory=list)  # (new prefix, old prefix)

    def real(self, p: Path) -> Path:
        """Where a planned path currently lives on disk (before any op is applied)."""
        k = _k(p)
        for new, old in reversed(self._dir_moves):
            if k == new or k.startswith(new + os.sep):
                return self.real(Path(old + k[len(new):]))
        return p

    # -- virtual filesystem so later planning steps see earlier ones ----------
    def exists(self, p: Path) -> bool:
        k = _k(p)
        if k in self._new:
            return True
        if k in self._gone:
            return False
        for g in self._gone:  # inside a directory that is being moved away
            if k.startswith(g + os.sep):
                return False
        r = self.real(p)
        if r != p:
            return r.exists()
        return p.exists()

    def _mark_move(self, src: Path, dst: Path) -> None:
        self._gone.add(_k(src))
        self._new.discard(_k(src))
        self._new.add(_k(dst))
        self._gone.discard(_k(dst))

    # -- builders -------------------------------------------------------------
    def mkdir(self, d: Path) -> None:
        if not self.exists(d):
            self.ops.append(Op("mkdir", d))
            self._new.add(_k(d))

    def move(self, src: Path, dst: Path, note: str = "") -> bool:
        if src == dst:
            return False
        rs, rd = self.real(src), self.real(dst)
        same_entry = rs.exists() and rd.exists() and _same(rs, rd)
        if self.exists(dst) and not same_entry:
            self.warnings.append(f"target exists, not moving: {src.name} -> {dst}")
            return False
        is_dir = self.real(src).is_dir()
        self.ops.append(Op("move", dst, src=src, note=note))
        self._mark_move(src, dst)
        if is_dir:
            self._dir_moves.append((_k(dst), _k(src)))
        return True

    def write(self, dst: Path, producer: Producer, note: str = "", overwrite: bool = True) -> None:
        if self.exists(dst) and not overwrite:
            return
        self.ops.append(Op("write", dst, producer=producer, note=note, overwrite=self.exists(dst)))
        self._new.add(_k(dst))

    def fetch(self, dst: Path, producer: Producer, note: str = "", force: bool = False) -> bool:
        if self.exists(dst) and not force:
            return False
        self.ops.append(Op("fetch", dst, producer=producer, note=note, overwrite=self.exists(dst)))
        self._new.add(_k(dst))
        return True

    def copy(self, src: Path, dst: Path, force: bool = False) -> None:
        if self.exists(dst) and not force:
            return
        self.ops.append(Op("copy", dst, src=src, overwrite=self.exists(dst)))
        self._new.add(_k(dst))

    def rmdir_if_empty(self, d: Path) -> None:
        self.ops.append(Op("rmdir_if_empty", d))

    @property
    def changes(self) -> list[Op]:
        return [o for o in self.ops if o.kind != "rmdir_if_empty"]


def _k(p: Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _same(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


# ---------------------------------------------------------------------------- apply
class Journal:
    """Undo log written to ~/.cache/mediamanager/undo/<timestamp>.json as we go."""

    def __init__(self, undo_dir: Path):
        self.dir = undo_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = self.dir / f"{stamp}-{uuid.uuid4().hex[:6]}.json"
        self.backups = self.dir / (self.path.stem + "-backup")
        self.entries: list[dict] = []

    def add(self, **entry) -> None:
        self.entries.append(entry)
        self.path.write_text(json.dumps(self.entries, indent=1), "utf-8")

    def backup(self, p: Path) -> str:
        self.backups.mkdir(exist_ok=True)
        dst = self.backups / f"{len(self.entries):05d}-{p.name}"
        shutil.copy2(p, dst)
        return str(dst)


def apply(plan: Plan, journal: Journal, on_error: Callable[[Op, Exception], None]) -> int:
    done = 0
    for op in plan.ops:
        try:
            if _apply_one(op, journal):
                done += 1
        except Exception as e:  # keep going; one failed download must not stop a run
            on_error(op, e)
    return done


def _apply_one(op: Op, j: Journal) -> bool:
    dst = op.dst
    if op.kind == "mkdir":
        if dst.exists():
            return False
        dst.mkdir(parents=True)
        j.add(op="mkdir", path=str(dst))
        return True
    if op.kind == "move":
        assert op.src is not None
        if not op.src.exists():
            raise FileNotFoundError(op.src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not _same(op.src, dst):
            raise FileExistsError(dst)
        if dst.exists():  # case-only rename on a case-insensitive filesystem
            tmp = op.src.with_name(op.src.name + f".mmtmp{uuid.uuid4().hex[:4]}")
            op.src.rename(tmp)
            tmp.rename(dst)
        else:
            shutil.move(str(op.src), str(dst))
        j.add(op="move", src=str(op.src), dst=str(dst))
        return True
    if op.kind in ("write", "fetch"):
        assert op.producer is not None
        data = op.producer()
        if not data:
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if dst.read_bytes() == data:
                return False
            j.add(op="overwrite", path=str(dst), backup=j.backup(dst))
        else:
            j.add(op="create", path=str(dst))
        tmp = dst.with_name(dst.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(dst)
        return True
    if op.kind == "copy":
        assert op.src is not None
        if not op.src.exists():
            return False
        if dst.exists():
            j.add(op="overwrite", path=str(dst), backup=j.backup(dst))
        else:
            j.add(op="create", path=str(dst))
        shutil.copy2(op.src, dst)
        return True
    if op.kind == "rmdir_if_empty":
        if dst.is_dir():
            for junk in (".DS_Store", "Thumbs.db"):
                if (dst / junk).exists() and len(list(dst.iterdir())) == 1:
                    (dst / junk).unlink()
            if not any(dst.iterdir()):
                dst.rmdir()
                j.add(op="rmdir", path=str(dst))
                return True
        return False
    raise ValueError(op.kind)


def undo(log_path: Path) -> list[str]:
    entries = json.loads(log_path.read_text("utf-8"))
    msgs = []
    for e in reversed(entries):
        try:
            if e["op"] == "move":
                src, dst = Path(e["src"]), Path(e["dst"])
                if dst.exists() and not src.exists():
                    src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(src))
            elif e["op"] == "create":
                Path(e["path"]).unlink(missing_ok=True)
            elif e["op"] == "overwrite":
                shutil.copy2(e["backup"], e["path"])
            elif e["op"] == "mkdir":
                p = Path(e["path"])
                if p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
            elif e["op"] == "rmdir":
                Path(e["path"]).mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            msgs.append(f"could not undo {e['op']} {e.get('path') or e.get('dst')}: {ex}")
    log_path.rename(log_path.with_suffix(".undone"))
    return msgs

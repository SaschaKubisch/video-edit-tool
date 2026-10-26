"""
The look (SPECS 8.2, 8.3; decisions D2 and D10).

The base look is the Blackmagic "Gen 5 Film to Video" LUT. A creative LUT is optional and
goes after it, on Rec.709. The owner's pick (2026-10-27) is "slay Film + Portrait": two
`.cube` files of the slay pack (CC BY 4.0), kept on the owner's drive in the folder that the
environment variable `VET_LUT_DIR` names, with a `CREDIT.txt` beside them. No LUT file is ever
committed to the repository.

A chosen creative LUT is copied to `<work_dir>/luts/<sha256>.cube`, so its path has no spaces
or characters that an ffmpeg filter string treats as syntax, and so the timeline file keeps
working if the original is moved. The file `look.lut` then holds that path, its hash,
`interp: tetrahedral`, the file's name and the credit line of the pack, if it has one.
"""

import copy
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

import safety

LUT_ENV = "VET_LUT_DIR"
CREDIT_FILE = "CREDIT.txt"
MAX_DEPTH = 3                 # how many folders below VET_LUT_DIR are searched


class LutError(Exception):
    """A LUT file cannot be used."""


def lut_dir(environ=None):
    """The folder named by VET_LUT_DIR, or None."""
    environ = os.environ if environ is None else environ
    return environ.get(LUT_ENV) or None


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_cube(path) -> dict:
    """Read a `.cube` file's header and count its entries, so that a broken file is refused
    here with a reason and not by ffmpeg with a cryptic one. Returns {"size", "title"}.
    Only 3D LUTs with the default domain (0 to 1) are accepted."""
    size, title, entries = None, "", 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                key = line.split(None, 1)[0]
                if key == "TITLE":
                    title = line[5:].strip().strip('"')
                elif key == "LUT_3D_SIZE":
                    try:
                        size = int(line.split()[1])
                    except (IndexError, ValueError):
                        raise LutError(f"{path}: LUT_3D_SIZE is not a number")
                elif key == "LUT_1D_SIZE":
                    raise LutError(f"{path}: this is a 1D LUT; the tool uses 3D LUTs")
                elif key in ("DOMAIN_MIN", "DOMAIN_MAX"):
                    values = line.split()[1:]
                    want = "0.0 0.0 0.0" if key == "DOMAIN_MIN" else "1.0 1.0 1.0"
                    if [float(v) for v in values] != [float(v) for v in want.split()]:
                        raise LutError(f"{path}: {key} is {' '.join(values)}; only the default "
                                       f"domain (0 to 1) is supported")
                elif key[0].isalpha():
                    continue                  # another keyword (LUT_IN_VIDEO_RANGE, ...)
                else:
                    entries += 1
    except OSError as e:
        raise LutError(f"cannot read {path}: {e}")
    if size is None:
        raise LutError(f"{path}: no LUT_3D_SIZE line, so this is not a 3D .cube file")
    if size < 2:
        raise LutError(f"{path}: LUT_3D_SIZE {size} is too small")
    if entries != size ** 3:
        raise LutError(f"{path}: LUT_3D_SIZE {size} needs {size ** 3} entries, the file has {entries}")
    return {"size": size, "title": title}


def read_credit(cube_path):
    """The credit line the pack asks for: the first line of `CREDIT.txt` in the folder of the
    file. None when there is no such file."""
    try:
        with open(Path(cube_path).with_name(CREDIT_FILE), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    return line.strip()
    except OSError:
        pass
    return None


def plain_credit(line: str) -> str:
    """The credit line as plain text for a description box: `[text](url)` becomes `text (url)`."""
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)


def list_luts(folder) -> list:
    """Every `.cube` file below `folder` (down to MAX_DEPTH folders), by id (the path relative
    to the folder, with `/`). A file that cannot be used has a `problem` and no `size`."""
    if not folder or not os.path.isdir(folder):
        return []
    root = Path(folder)
    rows = []
    for p in sorted(root.rglob("*.cube")):
        rel = p.relative_to(root)
        if len(rel.parts) > MAX_DEPTH or not p.is_file():
            continue
        row = {"id": rel.as_posix(), "name": p.name, "path": str(p), "credit": read_credit(p)}
        try:
            row.update(check_cube(p))
        except LutError as e:
            row["problem"] = str(e).replace(f"{p}: ", "")
            row["title"] = ""
        rows.append(row)
    return rows


def find_lut(folder, lut_id: str) -> str:
    """The path of the LUT with this id in `folder`. An id that leaves the folder (`..`, an
    absolute path, a link out) or names no file is refused."""
    if not folder or not os.path.isdir(folder):
        raise LutError("no LUT folder: set the environment variable VET_LUT_DIR (or pass --luts)")
    if not isinstance(lut_id, str) or not lut_id or os.path.isabs(lut_id):
        raise LutError(f"{lut_id!r} is not the id of a LUT in {folder}")
    root = os.path.realpath(folder)
    path = os.path.realpath(os.path.join(root, lut_id))
    if os.path.commonpath([root, path]) != root or not path.endswith(".cube") or not os.path.isfile(path):
        raise LutError(f"there is no LUT {lut_id!r} in {folder}")
    return path


def install(cube_path, work_dir: str, protected=None) -> dict:
    """Check the file, copy it to `<work_dir>/luts/<sha256>.cube` (once) and return the
    `look.lut` entry for it. `protected` lists folders of originals; the work folder must not
    lie inside one."""
    info = check_cube(cube_path)
    if protected:
        safety.assert_not_inside(work_dir, protected)
    digest = sha256_file(cube_path)
    folder = os.path.join(work_dir, "luts")
    target = os.path.join(folder, f"{digest}.cube")
    if not os.path.isfile(target) or sha256_file(target) != digest:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".lut-", suffix=".tmp")
        os.close(fd)
        try:
            shutil.copyfile(cube_path, tmp)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    entry = {"path": target, "sha256": digest, "interp": "tetrahedral",
             "name": Path(cube_path).name}
    credit = read_credit(cube_path)
    if credit:
        entry["credit"] = credit
    return entry


def with_creative_lut(data: dict, entry) -> dict:
    """A copy of the timeline data with `look.lut` set to `entry`, or removed when it is None."""
    out = copy.deepcopy(data)
    if entry is None:
        out["look"].pop("lut", None)
    else:
        out["look"]["lut"] = dict(entry)
    return out


def with_base_lut(data: dict, cube_path) -> dict:
    """A copy of the timeline data whose base look is the LUT at `cube_path` (with its hash)."""
    check_cube(cube_path)
    out = copy.deepcopy(data)
    out["look"]["base"] = {"mode": "lut", "path": str(cube_path), "sha256": sha256_file(cube_path)}
    return out


def film_credit(data: dict):
    """The credit line to publish with a film (plain text), or None when no LUT asks for one."""
    credit = ((data.get("look") or {}).get("lut") or {}).get("credit")
    return plain_credit(credit) if isinstance(credit, str) and credit.strip() else None

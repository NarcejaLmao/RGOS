"""RG OS file manager: browsing and file operations (no pygame here, so it can be tested on any PC).

menu.py draws the screen and feeds buttons into a FileManager. All paths stay inside the "roots", listed in
this order: the ROCKNIX boot partition, the optional second SD card, then ROMS. The boot files the device needs to
start (SYSTEM, KERNEL, extlinux, ...) are listed in "protected" and can't be deleted, renamed or moved from here.

Operations: copy, cut/move, paste, rename, new folder, delete, with multi-select (mark) and a
background worker so a long copy never freezes the menu.
"""
import os
import shutil
import threading

BAD_NAME_CHARS = '/\\\0'
MAX_NAME = 120


def human_size(n):
    """1536 -> '1.5 KB'."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def valid_name(name):
    """A single path component the user may create or rename to."""
    name = name.strip()
    if not name or name in (".", "..") or len(name) > MAX_NAME:
        return False
    return not any(c in name for c in BAD_NAME_CHARS)


def inside(path, root):
    """True if path is root or below it (after resolving symlinks and '..')."""
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def unique_name(folder, name):
    """name, or 'name (copy)', 'name (copy 2)', ... so a paste never overwrites anything."""
    if not os.path.lexists(os.path.join(folder, name)):
        return name
    stem, ext = os.path.splitext(name) if not os.path.isdir(os.path.join(folder, name)) else (name, "")
    n = 1
    while True:
        cand = "%s (copy%s)%s" % (stem, "" if n == 1 else " %d" % n, ext)
        if not os.path.lexists(os.path.join(folder, cand)):
            return cand
        n += 1


def list_dir(path):
    """Entries of a folder: folders first, then files, each sorted without regard to case. Hidden files are skipped."""
    out = []
    try:
        names = os.listdir(path)
    except OSError:
        return out
    for nm in names:
        if nm.startswith("."):
            continue
        p = os.path.join(path, nm)
        try:
            isdir = os.path.isdir(p)
            size = 0 if isdir else os.path.getsize(p)
        except OSError:
            continue
        out.append({"name": nm, "path": p, "dir": isdir, "size": size})
    out.sort(key=lambda e: (not e["dir"], e["name"].casefold()))
    return out


def tree_size(path):
    """Total bytes below path without following symlinks into other trees."""
    # A symlink is an item in its own right. Do not follow it when estimating
    # a folder's size: it may point outside the ROM storage or at a huge tree.
    try:
        if os.path.islink(path):
            return os.lstat(path).st_size
        if not os.path.isdir(path):
            return os.path.getsize(path)
    except OSError:
        return 0

    total = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        # os.walk lists symlinked directories in dirs even when it will not
        # descend into them. Count the link itself, not the target's contents.
        kept_dirs = []
        for name in dirs:
            p = os.path.join(base, name)
            if os.path.islink(p):
                try:
                    total += os.lstat(p).st_size
                except OSError:
                    pass
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            p = os.path.join(base, name)
            try:
                total += os.lstat(p).st_size
            except OSError:
                pass
    return total


def copy_item(src, dst_folder):
    """Copy a file or folder into dst_folder (never overwrites). Returns the new path.

    Treat a symlink itself as the item, even when it points to a directory. Otherwise
    copytree() would follow a directory symlink passed as the source and could copy
    data from outside the file manager's storage roots.
    """
    src_is_link = os.path.islink(src)
    src_is_dir = not src_is_link and os.path.isdir(src)
    if src_is_dir and inside(dst_folder, src):
        raise OSError("Cannot copy a folder into itself")
    dst = os.path.join(dst_folder, unique_name(dst_folder, os.path.basename(src)))
    if src_is_link:
        os.symlink(os.readlink(src), dst)
    elif src_is_dir:
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)
    return dst


def move_item(src, dst_folder):
    """Move a file or folder into dst_folder (never overwrites). Returns the new path."""
    if not os.path.islink(src) and os.path.isdir(src) and inside(dst_folder, src):
        raise OSError("Cannot move a folder into itself")
    if os.path.realpath(os.path.dirname(src)) == os.path.realpath(dst_folder):
        return src                                  # already there
    dst = os.path.join(dst_folder, unique_name(dst_folder, os.path.basename(src)))
    shutil.move(src, dst)
    return dst


def delete_item(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


class FileManager:
    """Browser state. cwd None = the list of storage roots; otherwise a folder inside one of the roots."""

    def __init__(self, roots, labels=None, protected=None):
        self.roots = [r for r in roots if r and os.path.isdir(r)]     # shown in this order
        self.labels = dict(labels or {})                              # root path -> name shown in the storage list
        # root path -> lower-case top-level names under it that may not be deleted, renamed or moved
        self.protected = {r: {n.casefold() for n in names} for r, names in (protected or {}).items()}
        self.cwd = None
        self.entries = []
        self.idx = 0
        self.marked = set()          # paths ticked with Y
        self.clip = []               # paths copied/cut, waiting for Paste
        self.clip_cut = False
        self.msg = ""
        self.busy = ""               # text shown while the worker thread runs ("" = idle)
        self._lock = threading.Lock()
        self.refresh()
        if len(self.roots) == 1:     # one card only: start inside it
            self.enter(self.roots[0])

    def label_of(self, root):
        return self.labels.get(root) or os.path.basename(root.rstrip("/")) or root

    def is_protected(self, path):
        """True for a protected top-level item of a root (or anything inside it)."""
        rp = os.path.realpath(path)
        for root, names in self.protected.items():
            rel = os.path.relpath(rp, os.path.realpath(root))
            if rel == "." or rel.startswith(".."):
                continue
            if rel.split(os.sep)[0].casefold() in names:
                return True
        return False

    # ---- navigation
    def refresh(self, keep=None):
        if self.cwd is None:
            self.entries = [{"name": self.label_of(r), "path": r, "dir": True, "size": 0, "root": True}
                            for r in self.roots]
        else:
            self.entries = list_dir(self.cwd)
        if keep:
            for i, e in enumerate(self.entries):
                if e["name"] == keep:
                    self.idx = i
                    break
        self.idx = max(0, min(self.idx, len(self.entries) - 1))
        self.marked &= {e["path"] for e in self.entries}

    def enter(self, path):
        if any(inside(path, r) for r in self.roots):
            self.cwd, self.idx = path, 0
            self.marked.clear()
            self.refresh()

    def current(self):
        return self.entries[self.idx] if self.entries else None

    def open_current(self):
        e = self.current()
        if e and e["dir"]:
            self.enter(e["path"])
            return True
        return False

    def at_top(self):
        """True when 'up' would leave the file manager (we are on the storage list, or inside the only root)."""
        if self.cwd is None:
            return True
        return len(self.roots) == 1 and os.path.realpath(self.cwd) == os.path.realpath(self.roots[0])

    def go_up(self):
        if self.at_top():
            return False
        if any(os.path.realpath(self.cwd) == os.path.realpath(r) for r in self.roots):
            self.cwd, self.idx = None, 0                  # back to the storage list
            self.marked.clear()
            self.refresh()
            return True
        came_from = os.path.basename(self.cwd)
        self.cwd = os.path.dirname(self.cwd)
        self.marked.clear()
        self.refresh(keep=came_from)
        return True

    def move_cursor(self, delta):
        if self.entries:
            self.idx = max(0, min(len(self.entries) - 1, self.idx + delta))

    def wrap_cursor(self, delta):
        if self.entries:
            self.idx = (self.idx + delta) % len(self.entries)

    def title(self):
        if self.cwd is None:
            return "Storage"
        for r in self.roots:
            if inside(self.cwd, r):
                rel = os.path.relpath(self.cwd, r)
                return self.label_of(r) + ("" if rel == "." else "/" + rel)
        return self.cwd

    # ---- selection
    def toggle_mark(self):
        e = self.current()
        if e and not e.get("root"):
            self.marked ^= {e["path"]}
            self.move_cursor(1)

    def targets(self):
        """Marked items, or the item under the cursor if nothing is marked."""
        if self.marked:
            return [e for e in self.entries if e["path"] in self.marked]
        e = self.current()
        return [e] if e and not e.get("root") else []

    # ---- clipboard
    def copy(self):
        t = self.targets()
        if t:
            self.clip, self.clip_cut, self.marked = [e["path"] for e in t], False, set()
            self.msg = "Copied %d item%s" % (len(t), "" if len(t) == 1 else "s")

    def cut(self):
        t = [e for e in self.targets() if not self.is_protected(e["path"])]
        if not t:
            if self.targets():
                self.msg = "Protected: needed to boot"
        else:
            self.clip, self.clip_cut, self.marked = [e["path"] for e in t], True, set()
            self.msg = "Cut %d item%s" % (len(t), "" if len(t) == 1 else "s")

    def can_paste(self):
        return bool(self.clip) and self.cwd is not None and not self.busy

    # ---- operations (paste/delete run on a worker thread; the rest are instant)
    def _run(self, text, fn):
        def job():
            try:
                done_msg = fn()
            except Exception as ex:                     # disk full, read-only card, permissions, ...
                done_msg = "Failed: %s" % (getattr(ex, "strerror", None) or ex)
            with self._lock:
                self.msg, self.busy = done_msg, ""
                self.refresh()
        self.busy = text
        t = threading.Thread(target=job, daemon=True)
        t.start()
        return t

    def paste(self):
        if not self.can_paste():
            return None
        clip, cut, dst = list(self.clip), self.clip_cut, self.cwd

        def fn():
            n = 0
            for src in clip:
                if not os.path.lexists(src):
                    continue
                (move_item if cut else copy_item)(src, dst)
                n += 1
            if cut:
                self.clip = []
            return "%s %d item%s" % ("Moved" if cut else "Pasted", n, "" if n == 1 else "s")
        return self._run("Moving..." if cut else "Copying...", fn)

    def delete(self):
        t = [e["path"] for e in self.targets()]
        if not t:
            return None
        roots = {os.path.realpath(r) for r in self.roots}

        def fn():
            n = skipped = 0
            for p in t:
                if os.path.realpath(p) in roots:        # never delete a whole card
                    continue
                if self.is_protected(p):                # boot files on the ROCKNIX partition
                    skipped += 1
                    continue
                delete_item(p)
                n += 1
            self.clip = [c for c in self.clip if os.path.lexists(c)]
            self.marked = set()
            msg = "Deleted %d item%s" % (n, "" if n == 1 else "s")
            return msg + (" (%d protected, kept)" % skipped if skipped else "")
        return self._run("Deleting...", fn)

    def rename(self, new_name):
        e = self.current()
        if not e or e.get("root"):
            return False
        if self.is_protected(e["path"]):
            self.msg = "Protected: needed to boot"
            return False
        new_name = new_name.strip()
        if not valid_name(new_name):
            self.msg = "Invalid name"
            return False
        if new_name == e["name"]:
            return True
        dst = os.path.join(os.path.dirname(e["path"]), new_name)
        if os.path.lexists(dst):
            self.msg = "'%s' already exists" % new_name
            return False
        try:
            os.rename(e["path"], dst)
        except OSError as ex:
            self.msg = "Failed: %s" % (ex.strerror or ex)
            return False
        self.msg = "Renamed"
        self.refresh(keep=new_name)
        return True

    def mkdir(self, name):
        if self.cwd is None:
            return False
        name = name.strip()
        if not valid_name(name):
            self.msg = "Invalid name"
            return False
        p = os.path.join(self.cwd, name)
        if os.path.lexists(p):
            self.msg = "'%s' already exists" % name
            return False
        try:
            os.mkdir(p)
        except OSError as ex:
            self.msg = "Failed: %s" % (ex.strerror or ex)
            return False
        self.msg = "Created folder"
        self.refresh(keep=name)
        return True

    def info(self):
        """Lines describing the item under the cursor (for the details popup)."""
        e = self.current()
        if not e:
            return []
        lines = [e["name"], "Folder" if e["dir"] else "File"]
        lines.append("Size: " + human_size(tree_size(e["path"])))
        if e.get("root"):
            try:
                st = os.statvfs(e["path"])
                lines.append("Free: %s of %s" % (human_size(st.f_bavail * st.f_frsize), human_size(st.f_blocks * st.f_frsize)))
            except OSError:
                pass
        return lines

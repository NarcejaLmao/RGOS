#!/usr/bin/env bash
# Backport Buildroot's official m4 1.4.21 package metadata to older Buildroot trees.
# This intentionally replaces only package/m4/m4.mk and package/m4/m4.hash from
# the upstream commit; git-apply is avoided because it can reject a valid update
# when the local checkout has unrelated index/worktree state.
set -Eeuo pipefail
BUILDROOT_DIR="${1:?usage: fix-host-m4.sh /path/to/buildroot}"
MK="$BUILDROOT_DIR/package/m4/m4.mk"
HASH="$BUILDROOT_DIR/package/m4/m4.hash"
[[ -f "$MK" && -f "$HASH" ]] || { echo "ERROR: expected Buildroot package files not found under $BUILDROOT_DIR/package/m4" >&2; exit 2; }
if grep -Eq '^M4_VERSION = 1\.4\.21$' "$MK"; then
  echo "host-m4 fix already present (m4 1.4.21)."
  exit 0
fi
if ! grep -Eq '^M4_VERSION = 1\.4\.(19|20)$' "$MK"; then
  echo "ERROR: unexpected m4 version; refusing to patch automatically:" >&2
  grep '^M4_VERSION' "$MK" >&2 || true
  exit 3
fi
commit=875d6aedbc639484e9203c5bcda1a55ecb7ad4c9
tmpref="refs/rgos/host-m4-fix"
tmpdir="$(mktemp -d)"
cleanup() { rm -rf "$tmpdir"; git -C "$BUILDROOT_DIR" update-ref -d "$tmpref" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "Fetching official Buildroot m4 1.4.21 update..."
git -C "$BUILDROOT_DIR" fetch --depth=1 https://gitlab.com/buildroot.org/buildroot.git "$commit:$tmpref"
# Read the exact files from the fetched upstream commit into temporary files first.
git -C "$BUILDROOT_DIR" show "$tmpref:package/m4/m4.mk" > "$tmpdir/m4.mk"
git -C "$BUILDROOT_DIR" show "$tmpref:package/m4/m4.hash" > "$tmpdir/m4.hash"
grep -Eq '^M4_VERSION = 1\.4\.21$' "$tmpdir/m4.mk" || { echo "ERROR: fetched commit does not contain m4 1.4.21" >&2; exit 4; }
grep -Eq 'm4-1\.4\.21\.tar\.xz' "$tmpdir/m4.hash" || { echo "ERROR: fetched hash file does not contain the 1.4.21 archive checksum" >&2; exit 4; }
# Keep backups so a failed copy can be rolled back.
cp -p "$MK" "$tmpdir/m4.mk.backup"
cp -p "$HASH" "$tmpdir/m4.hash.backup"
if ! cp "$tmpdir/m4.mk" "$MK" || ! cp "$tmpdir/m4.hash" "$HASH"; then
  cp "$tmpdir/m4.mk.backup" "$MK" || true
  cp "$tmpdir/m4.hash.backup" "$HASH" || true
  echo "ERROR: could not install upstream m4 metadata; original files restored where possible." >&2
  exit 5
fi
echo "Applied official host-m4 1.4.21 package metadata."
grep -E '^(M4_VERSION|M4_SOURCE)' "$MK"

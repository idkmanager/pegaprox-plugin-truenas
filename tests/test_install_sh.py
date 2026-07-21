# -*- coding: utf-8 -*-
"""Regression test for install.sh's redeploy-over-an-existing-install path.

Found live 2026-07-21: `cp -rf "$SRC/$f" "$DEST/$f"` for a directory item
(`src`) copies INTO an already-existing destination directory instead of
replacing its contents, nesting the whole tree at $DEST/src/src. On a
fresh install $DEST/src doesn't exist yet so this never showed up; on a
REdeploy (the common case) it silently left the OLD code being served
while manifest.json (a plain file, unaffected by this directory-specific
bug) correctly reported the new version — a half-applied deploy with no
error and a misleading version number.

install.sh itself needs root (it writes /etc/truenas-plugin.conf and talks
to systemd — see its own header comment), so it can't be exercised
end-to-end from an unprivileged test box. These tests instead (1) pin the
exact fixed source pattern so it can't silently regress, and (2) really
execute that pattern's bash/cp semantics in an unprivileged temp dir, which
is the part that actually varies by platform and is worth proving live
rather than just asserting a string is present.
"""

import os
import subprocess
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SH = os.path.join(REPO_ROOT, 'install.sh')


def _read_install_sh():
    with open(INSTALL_SH, encoding='utf-8') as f:
        return f.read()


def _seeded_src_and_dest(tmp):
    src, dest = os.path.join(tmp, 'src'), os.path.join(tmp, 'dest')
    os.makedirs(os.path.join(src, 'ui'))
    os.makedirs(os.path.join(dest, 'ui'))
    with open(os.path.join(src, 'ui', 'plugin.html'), 'w', encoding='utf-8') as f:
        f.write('NEW VERSION')
    with open(os.path.join(dest, 'ui', 'plugin.html'), 'w', encoding='utf-8') as f:
        f.write('OLD VERSION')
    return src, dest


def test_deploy_copy_loop_removes_destination_before_recreating_it():
    """Both places install.sh copies RUNTIME_ITEMS ($DEST and $CACHE_DIR)
    must rm the destination item before cp -rf'ing the source over it."""
    html = _read_install_sh()
    assert 'rm -rf "$DEST/$f"\n  cp -rf "$SRC/$f" "$DEST/$f"' in html
    assert 'rm -rf "$CACHE_DIR/$f"; cp -rf "$SRC/$f" "$CACHE_DIR/$f"' in html
    # The old, buggy pattern (cp -rf straight onto an existing destination,
    # nothing removing it first) must not reappear anywhere.
    assert 'for f in $RUNTIME_ITEMS; do cp -rf "$SRC/$f"' not in html


def test_rm_then_cp_r_replaces_an_existing_destination_dir_without_nesting():
    """Proves the actual fix pattern on THIS platform's bash/cp: copying a
    directory over an ALREADY-EXISTING destination of the same name must
    replace its contents, never nest a second copy inside it."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dest = _seeded_src_and_dest(tmp)
        result = subprocess.run(
            ['bash', '-c', 'rm -rf "$1" && cp -rf "$0" "$1"', src, dest],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr

        assert not os.path.isdir(os.path.join(dest, 'src')), \
            'nested a copy of src inside dest instead of replacing it'
        with open(os.path.join(dest, 'ui', 'plugin.html'), encoding='utf-8') as f:
            assert f.read() == 'NEW VERSION'


def test_bare_cp_r_onto_an_existing_dest_reproduces_the_original_bug():
    """Sanity check that the scenario above is real, not a tautology: the
    OLD pattern (no rm first) really does nest/leave stale content on this
    platform too. If this ever stops reproducing, the platform's cp
    semantics changed and the regression test above may need revisiting."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dest = _seeded_src_and_dest(tmp)
        result = subprocess.run(
            ['bash', '-c', 'cp -rf "$0" "$1"', src, dest],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr

        nested = os.path.isdir(os.path.join(dest, 'src'))
        with open(os.path.join(dest, 'ui', 'plugin.html'), encoding='utf-8') as f:
            still_old = f.read() == 'OLD VERSION'
        assert nested or still_old, (
            'expected the unfixed pattern to either nest src/ inside dest or '
            "leave the old file in place — if neither happened, this platform's "
            'cp does not reproduce the original bug and the scenario needs a '
            'different setup')

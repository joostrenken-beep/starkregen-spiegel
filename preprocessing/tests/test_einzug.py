"""Tests der Einzugsgebiets-Funktionen (M3) an künstlichem Gelände."""
import numpy as np

from starkregen import hydro


def _upstream_of(dem, cells):
    valid = np.isfinite(dem)
    f = hydro.fill_epsilon(dem, valid)
    down = hydro.d8_down(f, valid)
    ptr, idx = hydro.children_csr(down)
    stamp = np.zeros(dem.size, np.int32)
    up, trunc = hydro.upstream(np.array(cells, np.int64), ptr, idx, stamp, 1, dem.size)
    return set(up.tolist()), trunc, f


def test_plane_column():
    # Ebene fällt nach links (Spalte 0): Oberlieger einer Zelle = alle Zellen rechts davon in der Zeile
    n = 20
    dem = np.tile(10.0 + 0.01 * np.arange(n), (n, 1))
    up, trunc, _ = _upstream_of(dem, [10 * n + 5])
    assert not trunc
    assert {10 * n + c for c in range(5, n)} <= up


def test_pit_filled_and_drains():
    # Mulde in der Mitte wird gefüllt; ihr Wasser läuft weiter nach links und gehört zum Unterlieger
    n = 30
    dem = np.tile(10.0 + 0.01 * np.arange(n), (n, 1))
    dem[14:17, 14:17] -= 1.0
    up, _, f = _upstream_of(dem, [15 * n + 2])
    assert f[15, 15] >= dem[15, 14 - 1] - 1e-6        # Mulde aufgefüllt
    assert 15 * n + 15 in up                          # Muldenzelle liegt im Einzugsgebiet


def test_cap_truncates():
    n = 20
    dem = np.tile(10.0 + 0.01 * np.arange(n), (n, 1))
    valid = np.isfinite(dem)
    down = hydro.d8_down(hydro.fill_epsilon(dem, valid), valid)
    ptr, idx = hydro.children_csr(down)
    up, trunc = hydro.upstream(np.array([0], np.int64), ptr, idx, np.zeros(n * n, np.int32), 1, 5)
    assert trunc and len(up) == 5

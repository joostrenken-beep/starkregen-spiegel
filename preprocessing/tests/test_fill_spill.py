"""Funktionstests des Füll-und-Überlauf-Kerns an künstlichem Gelände mit bekannter Lösung.

Start:  python -m pytest preprocessing/tests -q
"""
import numpy as np
import pytest

from starkregen.model import analyse_terrain, run_fill_spill


def plane(n=100, slope=0.01):
    """Ebene, die zur linken Kante (x=0) hin abfällt."""
    x = np.arange(n, dtype=float)
    return np.tile(10.0 + slope * x, (n, 1))


def masks(dem):
    return np.ones_like(dem, bool), np.zeros_like(dem, bool)


def test_single_box_pit_partial_and_full():
    dem = plane()
    dem[40:50, 40:50] = 9.0                      # Kastensenke 10×10 m, Sohle 9,0 m
    valid, ns = masks(dem)
    h = analyse_terrain(dem, valid, ns)
    # Überlaufhöhe = tiefste Randzelle = Spalte 39 -> 10,39 m
    spill = 10.0 + 0.01 * 39
    cap = 100 * (spill - 9.0)
    rain = np.zeros_like(dem)

    # a) 50 m³ direkt in die Senke -> Spiegel 9,5 m, kein Überlauf
    rain[45, 45] = 50.0
    r = run_fill_spill(h, rain)
    assert r.lake_depth[45, 45] == pytest.approx(0.5, abs=1e-9)
    assert r.stored == pytest.approx(50.0)
    assert r.to_ocean == pytest.approx(0.0, abs=1e-9)

    # b) 200 m³ -> voll bis Überlaufhöhe, Rest verlässt das Gebiet
    rain[45, 45] = 200.0
    r = run_fill_spill(h, rain)
    assert r.lake_depth[45, 45] == pytest.approx(spill - 9.0, abs=1e-9)
    assert r.stored == pytest.approx(cap)
    assert r.to_ocean == pytest.approx(200.0 - cap)


def test_cascade_upper_pit_spills_into_lower():
    dem = plane()
    dem[40:50, 60:70] = 9.0   # obere Senke (bergauf)
    dem[40:50, 20:30] = 8.0   # untere Senke
    valid, ns = masks(dem)
    h = analyse_terrain(dem, valid, ns)
    up_spill = 10.0 + 0.01 * 59
    up_cap = 100 * (up_spill - 9.0)
    lo_spill = 10.0 + 0.01 * 19
    lo_cap = 100 * (lo_spill - 8.0)
    rain = np.zeros_like(dem)
    rain[45, 65] = up_cap + 30.0      # obere läuft mit 30 m³ über
    r = run_fill_spill(h, rain)
    assert r.lake_depth[45, 65] == pytest.approx(up_spill - 9.0, abs=1e-9)
    # 30 m³ landen unten (Überlauf fließt die Ebene hinab in die untere Senke)
    assert r.lake_depth[45, 25] == pytest.approx(0.30, abs=1e-6)
    assert r.stored == pytest.approx(up_cap + 30.0)
    assert lo_cap > 30


def test_two_pits_merge_into_one_lake():
    # Flaches Becken (Rand 12 m) mit zwei Teilsenken, getrennt durch Rücken 10,5 m
    n = 60
    dem = np.full((n, n), 12.0)
    dem[10:50, 10:50] = 11.0
    dem[20:40, 15:28] = 10.0   # Teilsenke links
    dem[20:40, 32:45] = 10.0   # Teilsenke rechts
    dem[20:40, 28:32] = 10.5   # Rücken dazwischen
    dem[30, 0:10] = 11.0       # Auslass Richtung Rand bei 11,0 m
    valid, ns = masks(dem)
    h = analyse_terrain(dem, valid, ns)
    rain = np.zeros_like(dem)
    # Nur links regnen: links füllt bis 10,5, läuft nach rechts, dann gemeinsamer Spiegel
    vol_each_to_ridge = 20 * 13 * 0.5
    rain[30, 20] = 2 * vol_each_to_ridge + 100.0
    r = run_fill_spill(h, rain)
    # gemeinsamer See über 2×260 + 4×20 (Rücken) Zellen ab 10,5 m
    area_above = 2 * 260 + 80
    expect = 10.5 + 100.0 / area_above
    assert r.lake_depth[30, 20] == pytest.approx(expect - 10.0, abs=1e-6)
    assert r.lake_depth[30, 40] == pytest.approx(expect - 10.0, abs=1e-6)
    assert r.stored + r.to_ocean == pytest.approx(rain.sum())


def test_mass_balance_random_terrain():
    rng = np.random.default_rng(1)
    from scipy.ndimage import gaussian_filter
    dem = gaussian_filter(rng.normal(size=(200, 200)), 4) * 5 + 20
    valid, ns = masks(dem)
    h = analyse_terrain(dem, valid, ns)
    for mm in (5, 30, 80, 500):
        rain = np.full_like(dem, mm / 1000)
        r = run_fill_spill(h, rain)
        assert r.stored + r.to_ocean == pytest.approx(r.runoff_total, rel=1e-9)
        # gespeichertes Volumen = Integral der Wassertiefe
        assert r.lake_depth.sum() == pytest.approx(r.stored, rel=1e-6)
        assert (r.lake_depth <= h.sink_depth_full() + 1e-9).all()


def test_carve_only_inside_corridor():
    from starkregen.model import breach_corridors
    dem = plane()
    dem[:, 50:53] += 3.0                       # Damm quer zum Tal
    dem[:3, 45:] += 10.0                       # Talflanken/Ränder oberhalb geschlossen
    dem[-3:, 45:] += 10.0
    dem[:, -3:] += 10.0
    valid, ns = masks(dem)
    corridor = np.zeros_like(valid)
    corridor[48:51, :] = True                  # Bach-Korridor über den Damm
    out, n, _ = breach_corridors(dem, valid, corridor)
    assert n > 0
    # im Korridor durchstochen: Damm nicht mehr höher als die Oberwasser-Sohle
    assert out[48:51, 51].min() <= dem[49, 53] + 1e-9
    # außerhalb des Korridors bleibt der Damm unverändert
    assert np.array_equal(out[10, 50:53], dem[10, 50:53])
    # danach keine Senke mehr hinter dem Damm im Korridor
    h = analyse_terrain(out, valid, ns)
    assert h.sink_depth_full()[49, 60] < 1e-6


def test_culvert_with_limited_capacity():
    # Tal mit Damm, Durchlass begrenzt: Teil fließt durch, Rest staut
    dem = plane()
    dem[:, 50:53] += 3.0
    dem[:3, 45:] += 10.0
    dem[-3:, 45:] += 10.0
    dem[:, -3:] += 10.0
    valid, ns = masks(dem)
    h = analyse_terrain(dem, valid, ns)
    nc = dem.shape[1]
    inlet, outlet = 49 * nc + 53, 49 * nc + 49
    rain = np.zeros_like(dem)
    rain[49, 70] = 1000.0
    r0 = run_fill_spill(h, rain)
    r1 = run_fill_spill(h, rain, [{"einlauf": inlet, "auslauf": outlet, "q_m3s": 0.1}], 3600)
    assert r1.durchlass_m3 == pytest.approx(360.0)
    assert r0.stored == pytest.approx(1000.0)
    assert r1.stored == pytest.approx(640.0)          # 360 m³ abgeleitet, Rest bleibt
    assert r1.stored + r1.to_ocean == pytest.approx(1000.0)
    assert r1.lake_depth[49, 60] < r0.lake_depth[49, 60]

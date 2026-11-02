"""Hydrologischer Kern: Senkenhierarchie und Füll-und-Überlauf (fill-and-spill).

Idee in Kurzform (für Bauingenieure):

1. Priority-Flood "von unten": Jede lokale Senke (Tiefpunkt) und der Gebietsrand
   ("Ozean" = Wasser verlässt das Gebiet) sind Startpunkte. Das Wasser steigt
   gedanklich überall gleichzeitig. Jede Zelle wird dem Startpunkt zugeordnet,
   den sie zuerst erreicht. Ergebnis je Zelle:
     - label  : zu welcher Senke (Teileinzugsgebiet) sie gehört
     - key    : Wasserspiegel, bei dem diese Zelle von "ihrer" Senke aus
                überflutet würde (>= Geländehöhe)
     - parent : Nachbarzelle in Richtung Tiefpunkt (Fließbaum, D8-artig)
   Wo zwei Gebiete aneinanderstoßen, entsteht eine "Kante" mit der
   Überlaufhöhe (Sattel).

2. Senkenhierarchie (Kruskal-Verfahren): Kanten werden nach Überlaufhöhe
   sortiert. Laufen zwei Senken bei Höhe z ineinander über, entsteht eine
   übergeordnete "Meta-Senke". Läuft eine Senke zum Rand über, wird sie zur
   Wurzel, die in ihr Unterlieger-Gebiet entwässert. Gleichzeitig wird das
   Fassungsvermögen jeder (Meta-)Senke bis zu ihrer Überlaufhöhe bestimmt.

3. Füll-und-Überlauf: Der Oberflächenabfluss jeder Zelle wird in "ihre" Senke
   gegeben. Ist sie voll, läuft der Überschuss über den Sattel in die
   Nachbarsenke; sind beide voll, steigt der gemeinsame Spiegel (Meta-Senke)
   usw. Das Endergebnis (statisch, maximale Füllung) ist unabhängig von der
   Reihenfolge.

Literatur: Barnes, Callaghan & Wickert (2020/2021), "Computing water flow
through complex landscapes", Parts 2 & 3 (Depression hierarchies,
Fill-Spill-Merge), Earth Surface Dynamics.

Alle Höhen in m, Volumen in m³ bei Zellfläche `cell_area` (m²).
"""
from __future__ import annotations

import numpy as np
from numba import njit

OCEAN = 0  # Label für "verlässt das Gebiet"

# 8er-Nachbarschaft
DR = np.array([-1, -1, -1, 0, 0, 1, 1, 1], dtype=np.int64)
DC = np.array([-1, 0, 1, -1, 1, -1, 0, 1], dtype=np.int64)
DIST = np.array([1.4142135623730951, 1.0, 1.4142135623730951, 1.0, 1.0,
                 1.4142135623730951, 1.0, 1.4142135623730951])


# ---------------------------------------------------------------------------
# Binärer Min-Heap (Schlüssel, Einfügezähler) -> stabile Reihenfolge auf Flächen
# ---------------------------------------------------------------------------
@njit(cache=True)
def _heap_push(hk, hc, hi, size, k, c, i):
    pos = size
    hk[pos] = k
    hc[pos] = c
    hi[pos] = i
    while pos > 0:
        par = (pos - 1) >> 1
        if hk[par] > hk[pos] or (hk[par] == hk[pos] and hc[par] > hc[pos]):
            hk[par], hk[pos] = hk[pos], hk[par]
            hc[par], hc[pos] = hc[pos], hc[par]
            hi[par], hi[pos] = hi[pos], hi[par]
            pos = par
        else:
            break
    return size + 1


@njit(cache=True)
def _heap_pop(hk, hc, hi, size):
    k = hk[0]
    i = hi[0]
    size -= 1
    hk[0] = hk[size]
    hc[0] = hc[size]
    hi[0] = hi[size]
    pos = 0
    while True:
        l = 2 * pos + 1
        r = l + 1
        m = pos
        if l < size and (hk[l] < hk[m] or (hk[l] == hk[m] and hc[l] < hc[m])):
            m = l
        if r < size and (hk[r] < hk[m] or (hk[r] == hk[m] and hc[r] < hc[m])):
            m = r
        if m == pos:
            break
        hk[m], hk[pos] = hk[pos], hk[m]
        hc[m], hc[pos] = hc[pos], hc[m]
        hi[m], hi[pos] = hi[pos], hi[m]
        pos = m
    return k, i, size


# ---------------------------------------------------------------------------
# 1. Priority-Flood mit Senken-Labels
# ---------------------------------------------------------------------------
@njit(cache=True)
def priority_flood_labels(dem, valid, no_seed):
    """Flutet das Gelände von allen Tiefpunkten und vom Rand aus.

    dem     : 2D float64, Geländehöhe (Gebäude bereits eingebrannt)
    valid   : 2D bool, gültige Zellen (False = NoData -> wirkt wie Rand)
    no_seed : 2D bool, Zellen, die kein Tiefpunkt sein dürfen (z. B. Dächer)

    Rückgabe: label, key, parent (flach, int64/float64), order (Abarbeitungs-
    reihenfolge), n_labels, edges (z, zelle_a, zelle_b)
    """
    nr, nc = dem.shape
    n = nr * nc
    flat = dem.ravel()
    vflat = valid.ravel()
    nsflat = no_seed.ravel()
    label = np.full(n, -1, dtype=np.int64)
    key = np.full(n, np.inf)
    parent = np.full(n, -1, dtype=np.int64)
    order = np.empty(n, dtype=np.int64)

    hk = np.empty(n, dtype=np.float64)
    hc = np.empty(n, dtype=np.int64)
    hi = np.empty(n, dtype=np.int64)
    size = 0
    counter = 0
    next_label = 1

    # Saatpunkte: Rand / Nachbarn von NoData -> OCEAN; lokale Minima -> neue Senke
    for r in range(nr):
        for c in range(nc):
            i = r * nc + c
            if not vflat[i]:
                continue
            edge = r == 0 or c == 0 or r == nr - 1 or c == nc - 1
            if not edge:
                for k in range(8):
                    j = (r + DR[k]) * nc + (c + DC[k])
                    if not vflat[j]:
                        edge = True
                        break
            if edge:
                label[i] = OCEAN
                key[i] = flat[i]
                size = _heap_push(hk, hc, hi, size, flat[i], counter, i)
                counter += 1
                continue
            if nsflat[i]:
                continue
            is_pit = True
            for k in range(8):
                j = (r + DR[k]) * nc + (c + DC[k])
                if flat[j] < flat[i]:
                    is_pit = False
                    break
            if is_pit:
                label[i] = next_label
                next_label += 1
                key[i] = flat[i]
                size = _heap_push(hk, hc, hi, size, flat[i], counter, i)
                counter += 1

    # Kantenliste (dynamisch wachsend)
    cap_e = 1 << 20
    ez = np.empty(cap_e, dtype=np.float64)
    ea = np.empty(cap_e, dtype=np.int64)
    eb = np.empty(cap_e, dtype=np.int64)
    ne = 0

    npop = 0
    while size > 0:
        kc, i, size = _heap_pop(hk, hc, hi, size)
        order[npop] = i
        npop += 1
        r = i // nc
        c = i % nc
        li = label[i]
        for k in range(8):
            rr = r + DR[k]
            cc = c + DC[k]
            if rr < 0 or cc < 0 or rr >= nr or cc >= nc:
                continue
            j = rr * nc + cc
            if not vflat[j]:
                continue
            lj = label[j]
            if lj == -1:
                label[j] = li
                kj = flat[j] if flat[j] > kc else kc
                key[j] = kj
                parent[j] = i
                size = _heap_push(hk, hc, hi, size, kj, counter, j)
                counter += 1
            elif lj != li and not (lj == OCEAN and li == OCEAN):
                z = kc if kc > key[j] else key[j]
                if ne == cap_e:
                    cap_e *= 2
                    ez2 = np.empty(cap_e, dtype=np.float64)
                    ea2 = np.empty(cap_e, dtype=np.int64)
                    eb2 = np.empty(cap_e, dtype=np.int64)
                    ez2[:ne] = ez[:ne]
                    ea2[:ne] = ea[:ne]
                    eb2[:ne] = eb[:ne]
                    ez, ea, eb = ez2, ea2, eb2
                ez[ne] = z
                ea[ne] = i
                eb[ne] = j
                ne += 1
    return label, key, parent, order[:npop], next_label, ez[:ne], ea[:ne], eb[:ne]


# ---------------------------------------------------------------------------
# 2. Senkenhierarchie mit Fassungsvermögen
# ---------------------------------------------------------------------------
@njit(cache=True)
def _find(uf, x):
    root = x
    while uf[root] != root:
        root = uf[root]
    while uf[x] != root:
        nxt = uf[x]
        uf[x] = root
        x = nxt
    return root


@njit(cache=True)
def build_hierarchy(dem_flat, label, key, n_labels, ez, ea, eb, cell_area):
    """Baut den Senkenbaum.

    Knoten 1..n_labels-1 sind Blätter (= Einzelsenken), höhere IDs sind
    Meta-Senken. Knoten 0 = Ozean (unbenutzt).

    Rückgabe je Knoten:
      cap      : Fassungsvermögen bis zur Überlaufhöhe (m³), inkl. Kinder
      own_cap  : zusätzliches Volumen dieses Knotens oberhalb der Kinder
      zspill   : Überlaufhöhe (m)
      parent   : Elternknoten (-1 = Wurzel, läuft in anderes Gebiet über)
      sibling  : Geschwisterknoten
      link     : Zelle, in die der Überlauf eintritt (-1 = Rand)
      child_a, child_b
      n_nodes
    """
    L = n_labels
    max_nodes = 2 * L + 1
    cap = np.zeros(max_nodes)
    own_cap = np.zeros(max_nodes)
    zspill = np.full(max_nodes, np.inf)
    parent = np.full(max_nodes, -1, dtype=np.int64)
    sibling = np.full(max_nodes, -1, dtype=np.int64)
    link = np.full(max_nodes, -1, dtype=np.int64)
    child_a = np.full(max_nodes, -1, dtype=np.int64)
    child_b = np.full(max_nodes, -1, dtype=np.int64)

    # Union-Find über Komponenten (Index = Label bzw. neuer Knoten)
    uf = np.arange(max_nodes)
    top = np.arange(max_nodes)          # aktueller Spitzenknoten der Komponente
    is_ocean = np.zeros(max_nodes, dtype=np.bool_)
    is_ocean[OCEAN] = True
    cnt = np.zeros(max_nodes)           # Zellen unter dem aktuellen Niveau
    sdem = np.zeros(max_nodes)          # Summe deren Geländehöhen

    # Zellen nach key sortieren (Sweep)
    cell_order = np.argsort(key)
    e_order = np.argsort(ez)
    n_cells = key.shape[0]
    pc = 0
    n_nodes = L

    for t in range(e_order.shape[0]):
        e = e_order[t]
        z = ez[e]
        # alle Zellen mit key < z einsortieren
        while pc < n_cells and key[cell_order[pc]] < z:
            ci = cell_order[pc]
            lc = label[ci]
            if lc > 0:
                rc = _find(uf, lc)
                if not is_ocean[rc]:
                    cnt[rc] += 1.0
                    sdem[rc] += dem_flat[ci]
            pc += 1
        a_cell = ea[e]
        b_cell = eb[e]
        la = label[a_cell]
        lb = label[b_cell]
        ra = _find(uf, la)
        rb = _find(uf, lb)
        if ra == rb:
            continue
        oa = is_ocean[ra]
        ob = is_ocean[rb]
        if oa and ob:
            continue
        if not oa and not ob:
            A = top[ra]
            B = top[rb]
            capA = (z * cnt[ra] - sdem[ra]) * cell_area
            capB = (z * cnt[rb] - sdem[rb]) * cell_area
            if capA < 0.0:
                capA = 0.0
            if capB < 0.0:
                capB = 0.0
            for X, cX in ((A, capA), (B, capB)):
                cap[X] = cX
                zspill[X] = z
                ch_sum = 0.0
                if child_a[X] >= 0:
                    ch_sum = cap[child_a[X]] + cap[child_b[X]]
                oc = cX - ch_sum
                own_cap[X] = oc if oc > 0.0 else 0.0
            M = n_nodes
            n_nodes += 1
            parent[A] = M
            parent[B] = M
            sibling[A] = B
            sibling[B] = A
            link[A] = b_cell   # Überlauf aus A tritt auf B-Seite ein
            link[B] = a_cell
            child_a[M] = A
            child_b[M] = B
            # Komponenten vereinen
            uf[rb] = ra
            cnt[ra] += cnt[rb]
            sdem[ra] += sdem[rb]
            top[ra] = M
        else:
            # eine Seite ist bereits mit dem Rand verbunden
            if oa:
                ra, rb = rb, ra
                a_cell, b_cell = b_cell, a_cell
            A = top[ra]
            capA = (z * cnt[ra] - sdem[ra]) * cell_area
            if capA < 0.0:
                capA = 0.0
            cap[A] = capA
            zspill[A] = z
            ch_sum = 0.0
            if child_a[A] >= 0:
                ch_sum = cap[child_a[A]] + cap[child_b[A]]
            oc = capA - ch_sum
            own_cap[A] = oc if oc > 0.0 else 0.0
            parent[A] = -1
            link[A] = b_cell   # Zelle im Unterlieger-Gebiet (oder Rand)
            uf[ra] = rb
            # Ozean-Komponente bleibt Ozean
    return (cap[:n_nodes], own_cap[:n_nodes], zspill[:n_nodes], parent[:n_nodes],
            sibling[:n_nodes], link[:n_nodes], child_a[:n_nodes], child_b[:n_nodes], n_nodes)


# ---------------------------------------------------------------------------
# 3. Füll-und-Überlauf
# ---------------------------------------------------------------------------
@njit(cache=True)
def fill_spill(label, runoff_by_label, own_cap, parent, sibling, link, n_nodes):
    """Verteilt das Abflussvolumen (m³ je Einzelsenke) auf die Senkenhierarchie.

    Rückgabe:
      water   : gespeichertes Volumen je Knoten (Anteil oberhalb der Kinder, m³)
      isfull  : Knoten bis zur Überlaufhöhe gefüllt
      over    : über den Sattel (link) abgegebenes Volumen je Knoten (m³)
      to_ocean: Volumen, das das Gebiet verlässt (m³)
    """
    water = np.zeros(n_nodes)
    isfull = np.zeros(n_nodes, dtype=np.bool_)
    over = np.zeros(n_nodes)
    to_ocean = 0.0
    for L in range(1, runoff_by_label.shape[0]):
        amt = runoff_by_label[L]
        if amt <= 0.0:
            continue
        X = L
        while amt > 0.0:
            if not isfull[X]:
                room = own_cap[X] - water[X]
                if amt <= room:
                    water[X] += amt
                    amt = 0.0
                    break
                if room > 0.0:
                    amt -= room
                water[X] = own_cap[X]
                isfull[X] = True
            # X ist voll -> Überlauf
            if parent[X] == -1:
                over[X] += amt
                tgt = link[X]
                if tgt < 0 or label[tgt] == OCEAN:
                    to_ocean += amt
                    amt = 0.0
                    break
                X = label[tgt]
                continue
            S = sibling[X]
            if isfull[S]:
                X = parent[X]            # beide voll -> gemeinsamer Spiegel steigt
            else:
                over[X] += amt
                X = label[link[X]]       # Überlauf in die Nachbarsenke
    return water, isfull, over, to_ocean


@njit(cache=True)
def root_nodes(parent, n_nodes):
    """Wurzel (oberste Senke) je Knoten – linear: ein Elternknoten entsteht immer
    nach seinen Kindern und hat daher die höhere Nummer."""
    root = np.arange(n_nodes)
    for x in range(n_nodes - 1, -1, -1):
        p = parent[x]
        if p >= 0:
            root[x] = root[p]
    return root


@njit(cache=True)
def fill_spill_drains(label, runoff_by_label, own_cap, parent, sibling, link, n_nodes,
                      root, drain_cap, drain_target):
    """Füll-und-Überlauf mit Durchlässen begrenzter Leistung.

    drain_cap[R]   : Volumen (m³), das ein Durchlass aus dem Senkensystem mit Wurzel R
                     während des Ereignisses ableiten kann (= Q_max · Dauer); 0 = keiner
    drain_target[R]: Zelle am Auslauf des Durchlasses

    Wasser, das ein Senkensystem mit Durchlass erreicht, fließt zuerst durch den
    Durchlass (bis drain_cap), erst danach füllt es die Senke (statische Näherung).
    Rückgabe: water, isfull, over, to_ocean, drained (je Knoten)
    """
    water = np.zeros(n_nodes)
    isfull = np.zeros(n_nodes, dtype=np.bool_)
    over = np.zeros(n_nodes)
    drained = np.zeros(n_nodes)
    rem = drain_cap.copy()
    to_ocean = 0.0
    n_lab = runoff_by_label.shape[0]
    st_x = np.empty(16, dtype=np.int64)
    st_a = np.empty(16)
    for L0 in range(1, n_lab):
        if runoff_by_label[L0] <= 0.0:
            continue
        sp = 0
        st_x[0] = L0
        st_a[0] = runoff_by_label[L0]
        sp = 1
        while sp > 0:
            sp -= 1
            X = st_x[sp]
            amt = st_a[sp]
            while amt > 0.0:
                R = root[X]
                if rem[R] > 0.0:
                    d = amt if amt < rem[R] else rem[R]
                    rem[R] -= d
                    drained[R] += d
                    amt -= d
                    tgt = drain_target[R]
                    if tgt < 0 or label[tgt] == OCEAN:
                        to_ocean += d
                    else:
                        if sp + 1 >= st_x.shape[0]:
                            sx = np.empty(st_x.shape[0] * 2, dtype=np.int64)
                            sa = np.empty(st_x.shape[0] * 2)
                            sx[:sp] = st_x[:sp]
                            sa[:sp] = st_a[:sp]
                            st_x, st_a = sx, sa
                        st_x[sp] = label[tgt]
                        st_a[sp] = d
                        sp += 1
                    if amt <= 0.0:
                        break
                if not isfull[X]:
                    room = own_cap[X] - water[X]
                    if amt <= room:
                        water[X] += amt
                        amt = 0.0
                        break
                    if room > 0.0:
                        amt -= room
                    water[X] = own_cap[X]
                    isfull[X] = True
                if parent[X] == -1:
                    over[X] += amt
                    tgt = link[X]
                    if tgt < 0 or label[tgt] == OCEAN:
                        to_ocean += amt
                        amt = 0.0
                        break
                    X = label[tgt]
                    continue
                S = sibling[X]
                if isfull[S]:
                    X = parent[X]
                else:
                    over[X] += amt
                    X = label[link[X]]
    return water, isfull, over, to_ocean, drained


# ---------------------------------------------------------------------------
# 4. Wasserspiegel je Senke und Wassertiefe je Zelle
# ---------------------------------------------------------------------------
@njit(cache=True)
def _collect_leaves(X, child_a, child_b, out, n_out):
    stack = np.empty(64, dtype=np.int64)
    sp = 0
    stack[sp] = X
    sp += 1
    while sp > 0:
        sp -= 1
        Y = stack[sp]
        if child_a[Y] < 0:
            out[n_out] = Y
            n_out += 1
        else:
            if sp + 2 >= stack.shape[0]:
                s2 = np.empty(stack.shape[0] * 2, dtype=np.int64)
                s2[:sp] = stack[:sp]
                stack = s2
            stack[sp] = child_a[Y]
            stack[sp + 1] = child_b[Y]
            sp += 2
    return n_out


@njit(cache=True)
def lake_levels(dem_flat, key, label, water, isfull, cap, own_cap, zspill, parent,
                child_a, child_b, n_nodes, n_labels, cell_area):
    """Bestimmt den Wasserspiegel jeder Einzelsenke (Blatt) und die Tiefe je Zelle."""
    # gesamtes Volumen im Teilbaum eines Knotens
    total = np.zeros(n_nodes)
    for X in range(n_nodes):
        if child_a[X] < 0:
            total[X] = water[X]
        else:
            total[X] = cap[child_a[X]] + cap[child_b[X]] + water[X]

    # aktive Spitzenknoten: Wasser > 0 und Eltern ohne eigenes Wasser
    active = np.zeros(n_nodes, dtype=np.bool_)
    for X in range(1, n_nodes):
        if water[X] > 0.0 or isfull[X]:
            p = parent[X]
            if p < 0 or not (water[p] > 0.0 or isfull[p]):
                active[X] = True

    # Zellen nach Label gruppieren (CSR)
    n_cells = label.shape[0]
    counts = np.zeros(n_labels + 1, dtype=np.int64)
    for i in range(n_cells):
        if label[i] > 0:
            counts[label[i] + 1] += 1
    start = np.cumsum(counts)
    fillpos = start[:-1].copy()
    cells_by = np.empty(start[-1], dtype=np.int64)
    for i in range(n_cells):
        l = label[i]
        if l > 0:
            cells_by[fillpos[l]] = i
            fillpos[l] += 1

    leaf_level = np.full(n_labels, -np.inf)
    leaves = np.empty(n_labels, dtype=np.int64)
    for X in range(1, n_nodes):
        if not active[X]:
            continue
        nl = _collect_leaves(X, child_a, child_b, leaves, 0)
        if isfull[X]:
            lvl = zspill[X]
        else:
            # Zellen des Teilbaums mit key < zspill sammeln und Spiegel lösen
            m = 0
            for q in range(nl):
                lf = leaves[q]
                m += start[lf + 1] - start[lf]
            ks = np.empty(m)
            ds = np.empty(m)
            m = 0
            zs = zspill[X]
            for q in range(nl):
                lf = leaves[q]
                for t in range(start[lf], start[lf + 1]):
                    ci = cells_by[t]
                    if key[ci] < zs:
                        ks[m] = key[ci]
                        ds[m] = dem_flat[ci]
                        m += 1
            ks = ks[:m]
            ds = ds[:m]
            o = np.argsort(ks)
            V = total[X] / cell_area
            sumd = 0.0
            lvl = zs
            for t in range(m):
                sumd += ds[o[t]]
                nxt = ks[o[t + 1]] if t + 1 < m else zs
                # Spiegel zwischen ks[o[t]] und nxt: V = (t+1)*z - sumd
                zcand = (V + sumd) / (t + 1)
                if zcand <= nxt:
                    lvl = zcand
                    break
        for q in range(nl):
            lf = leaves[q]
            if lvl > leaf_level[lf]:
                leaf_level[lf] = lvl

    depth = np.zeros(n_cells)
    for i in range(n_cells):
        l = label[i]
        if l > 0:
            lv = leaf_level[l]
            if lv > -np.inf and key[i] < lv:
                d = lv - dem_flat[i]
                if d > 0.0:
                    depth[i] = d
    return depth, leaf_level


# ---------------------------------------------------------------------------
# 5. Fließwege: Mehrfach-Fließrichtungen (MFD, Quinn/Freeman) mit Senkenrückhalt
# ---------------------------------------------------------------------------
@njit(cache=True)
def mfd_accumulate(dem, valid, source, absorb, exponent=1.1):
    """Leitet Volumen (m³) abwärts auf alle tieferen Nachbarn (gewichtet nach
    Gefälle^exponent × Konturlänge). Zellen mit absorb=True (Wasserflächen der
    Senken) nehmen ankommendes Wasser auf und geben nichts weiter.

    Rückgabe: acc (m³, durch die Zelle geflossenes Volumen), slope (m/m, steilstes
    Gefälle abwärts)
    """
    nr, nc = dem.shape
    n = nr * nc
    flat = dem.ravel()
    vflat = valid.ravel()
    acc = source.ravel().copy()
    slope = np.zeros(n)
    order = np.argsort(-flat)
    ab = absorb.ravel()
    cl = np.array([0.354, 0.5, 0.354, 0.5, 0.5, 0.354, 0.5, 0.354])
    w = np.zeros(8)
    for t in range(n):
        i = order[t]
        if not vflat[i]:
            continue
        r = i // nc
        c = i % nc
        smax = 0.0
        wsum = 0.0
        for k in range(8):
            rr = r + DR[k]
            cc = c + DC[k]
            w[k] = 0.0
            if rr < 0 or cc < 0 or rr >= nr or cc >= nc:
                continue
            j = rr * nc + cc
            if not vflat[j]:
                continue
            s = (flat[i] - flat[j]) / DIST[k]
            if s > 0.0:
                if s > smax:
                    smax = s
                w[k] = (s ** exponent) * cl[k]
                wsum += w[k]
        slope[i] = smax
        if ab[i] or wsum == 0.0:
            continue
        a = acc[i]
        if a == 0.0:
            continue
        for k in range(8):
            if w[k] > 0.0:
                j = (r + DR[k]) * nc + (c + DC[k])
                acc[j] += a * w[k] / wsum
    return acc.reshape(nr, nc), slope.reshape(nr, nc)


@njit(cache=True)
def filled_dem(dem_flat, key, label, n_labels, zspill_leaf_root):
    """Gefülltes DGM (alle Senken voll) aus der Kette der Überlaufhöhen."""
    out = dem_flat.copy()
    for i in range(dem_flat.shape[0]):
        l = label[i]
        if l > 0:
            z = zspill_leaf_root[l]
            if key[i] < z and z > out[i]:
                out[i] = z
    return out


# ---------------------------------------------------------------------------
# 6. Eingeschränktes Durchstechen (least-cost breaching) entlang Gewässern
# ---------------------------------------------------------------------------
@njit(cache=True)
def fill_depressions(dem, valid):
    """Klassisches Priority-Flood-Füllen (Barnes 2014): alle Senken bis zum Überlauf."""
    nr, nc = dem.shape
    n = nr * nc
    z = dem.ravel()
    vflat = valid.ravel()
    out = np.full(n, np.nan)
    hk = np.empty(n, dtype=np.float64)
    hc = np.empty(n, dtype=np.int64)
    hi = np.empty(n, dtype=np.int64)
    size = 0
    counter = 0
    for r in range(nr):
        for c in range(nc):
            i = r * nc + c
            if not vflat[i]:
                continue
            edge = r == 0 or c == 0 or r == nr - 1 or c == nc - 1
            if not edge:
                for k in range(8):
                    if not vflat[(r + DR[k]) * nc + (c + DC[k])]:
                        edge = True
                        break
            if edge:
                out[i] = z[i]
                size = _heap_push(hk, hc, hi, size, z[i], counter, i)
                counter += 1
    while size > 0:
        kc, i, size = _heap_pop(hk, hc, hi, size)
        r = i // nc
        c = i % nc
        for k in range(8):
            rr = r + DR[k]
            cc = c + DC[k]
            if rr < 0 or cc < 0 or rr >= nr or cc >= nc:
                continue
            j = rr * nc + cc
            if not vflat[j] or not np.isnan(out[j]):
                continue
            out[j] = z[j] if z[j] > kc else kc
            size = _heap_push(hk, hc, hi, size, out[j], counter, j)
            counter += 1
    return out.reshape(nr, nc)


@njit(cache=True)
def breach_from(z, valid, corridor, dep_label, start, max_len, cost, steps, prev, touched,
                max_cut=-1.0):
    """Dijkstra im Korridor ab Senkentiefpunkt `start`: sucht den Weg mit dem
    geringsten Aushub (Summe der Höhen über dem Startpunkt) zu einer Zelle, die
    tiefer liegt und nicht zur selben Senke gehört (oder am Gebietsrand liegt).
    Senkt den Weg linear zwischen Start- und Zielhöhe ab.

    max_cut >= 0: Wege mit tieferem Aushub werden NICHT ausgehoben (Damm bleibt),
    sondern nur gemeldet -> Durchlass mit begrenzter Leistung.
    Rückgabe: (abgesenkte Zellen, Zielzelle, max. Aushub, Zelle des max. Aushubs)"""
    nr, nc = z.shape
    zf = z.ravel()
    vflat = valid.ravel()
    cor = corridor.ravel()
    lab = dep_label.ravel()
    zs = zf[start]
    my = lab[start]
    hk = np.empty(64, dtype=np.float64)
    hc = np.empty(64, dtype=np.int64)
    hi = np.empty(64, dtype=np.int64)
    size = 0
    counter = 0
    nt = 0
    cost[start] = 0.0
    steps[start] = 0
    prev[start] = -1
    touched[nt] = start
    nt += 1
    size = _heap_push(hk, hc, hi, size, 0.0, counter, start)
    counter += 1
    target = -1
    while size > 0:
        kc, i, size = _heap_pop(hk, hc, hi, size)
        if kc > cost[i]:
            continue
        r = i // nc
        c = i % nc
        if i != start and zf[i] < zs - 1e-3 and lab[i] != my:
            target = i
            break
        if r == 0 or c == 0 or r == nr - 1 or c == nc - 1:
            if i != start and zf[i] <= zs:
                target = i
                break
        if steps[i] >= max_len:
            continue
        found = False
        for k in range(8):
            rr = r + DR[k]
            cc = c + DC[k]
            if rr < 0 or cc < 0 or rr >= nr or cc >= nc:
                continue
            j = rr * nc + cc
            if not vflat[j]:
                continue
            if not cor[j]:
                # Auslauf in tieferes Gelände außerhalb des Korridors (z. B. nicht erfasster Graben)
                if i != start and zf[j] < zs - 1e-3 and lab[j] != my:
                    prev[j] = i
                    steps[j] = steps[i] + 1
                    if cost[j] == np.inf and nt < touched.shape[0]:
                        cost[j] = kc
                        touched[nt] = j
                        nt += 1
                    target = j
                    found = True
                    break
                continue
            nc_ = kc + max(0.0, zf[j] - zs) + 1e-3 * DIST[k]
            if nc_ < cost[j]:
                if cost[j] == np.inf:
                    if nt >= touched.shape[0]:
                        continue
                    touched[nt] = j
                    nt += 1
                cost[j] = nc_
                steps[j] = steps[i] + 1
                prev[j] = i
                if size >= hk.shape[0]:
                    hk2 = np.empty(hk.shape[0] * 2, dtype=np.float64)
                    hc2 = np.empty(hk.shape[0] * 2, dtype=np.int64)
                    hi2 = np.empty(hk.shape[0] * 2, dtype=np.int64)
                    hk2[:size] = hk[:size]
                    hc2[:size] = hc[:size]
                    hi2[:size] = hi[:size]
                    hk, hc, hi = hk2, hc2, hi2
                size = _heap_push(hk, hc, hi, size, nc_, counter, j)
                counter += 1
        if found:
            break
    m = 0
    maxcut = 0.0
    crest = -1
    if target >= 0:
        zt = zf[target]
        L = steps[target]
        p = prev[target]
        while p >= 0 and p != start:
            zi = zs + (zt - zs) * (steps[p] / max(L, 1))
            if zf[p] - zi > maxcut:
                maxcut = zf[p] - zi
                crest = p
            p = prev[p]
        if max_cut < 0.0 or maxcut <= max_cut:
            p = prev[target]
            while p >= 0 and p != start:
                zi = zs + (zt - zs) * (steps[p] / max(L, 1))
                if zf[p] > zi:
                    zf[p] = zi
                    m += 1
                p = prev[p]
    for t in range(nt):
        cost[touched[t]] = np.inf
    return m, target, maxcut, crest


@njit(cache=True)
def d8_accumulate(dem, valid, source, absorb):
    """D8 (steilster Abstieg): Fließrichtung je Zelle und durchgeflossenes Volumen.
    Zellen ohne tieferen Nachbarn oder mit absorb=True (Wasserfläche) geben nichts weiter.
    Rückgabe: acc (m³), down (Index der Unterliegerzelle, -1 = keine)."""
    nr, nc = dem.shape
    n = nr * nc
    z = dem.ravel()
    v = valid.ravel()
    ab = absorb.ravel()
    acc = source.ravel().copy()
    down = np.full(n, -1, dtype=np.int64)
    order = np.argsort(-z)
    for t in range(n):
        i = order[t]
        if not v[i]:
            continue
        r = i // nc
        c = i % nc
        best = 0.0
        bj = -1
        for k in range(8):
            rr = r + DR[k]
            cc = c + DC[k]
            if rr < 0 or cc < 0 or rr >= nr or cc >= nc:
                continue
            j = rr * nc + cc
            if not v[j]:
                continue
            s = (z[i] - z[j]) / DIST[k]
            if s > best:
                best = s
                bj = j
        down[i] = bj
        if bj >= 0 and not ab[i]:
            acc[bj] += acc[i]
    return acc.reshape(nr, nc), down

"""Signature -> mineral / category lookup.

Every entry is `base * count`, where count is the number of rocks (or debris
pieces / deposits) in a cluster, from 1 up to a per-entry maximum. Capping the
count removes impossible high-count guesses, which is what keeps identification
accurate.

Round-number signatures (multiples of 2000 / 3000 / 4000) can genuinely be
more than one of Salvage / FPS Mineables / ROC Mineables, so a lookup returns
*every* tied candidate rather than guessing one.

Pure standard library, so it can be unit-tested on its own:
    python minerals.py
"""

# (name, base signature for 1 rock, max rocks). value == base * count.
ENTRIES = [
    ("Quantainium", 3170, 2),
    ("Stileron", 3185, 2),
    ("Savrilium", 3200, 2),
    ("Ouratite", 3370, 3),
    ("Riccite", 3385, 3),
    ("Lindinium", 3400, 3),
    ("Beryl", 3540, 4),
    ("Taranite", 3555, 4),
    ("Borase", 3570, 4),
    ("Gold", 3585, 4),
    ("Bexalite", 3600, 4),
    ("Laranite", 3825, 5),
    ("Aslarite", 3840, 5),
    ("Titanium", 3855, 5),
    ("Tungsten", 3870, 5),
    ("Agricium", 3885, 5),
    ("Torite", 3900, 5),
    ("Hephaestanite", 4180, 6),
    ("Tin", 4195, 6),
    ("Quartz", 4210, 6),
    ("Corundum", 4225, 6),
    ("Copper", 4240, 6),
    ("Silicon", 4255, 6),
    ("Iron", 4270, 6),
    ("Aluminium", 4285, 6),
    ("Ice", 4300, 6),
    # Non-mineral categories — same base * count model.
    ("Salvage/Harvestables", 2000, 15),   # up to 30,000
    ("FPS Mineables", 3000, 20),          # up to 60,000
    ("ROC Mineables", 4000, 20),          # up to 80,000 (20 rocks) in game
]

# Short labels for the compact multi-candidate display.
SHORT_NAMES = {
    "Salvage/Harvestables": "Salvage",
    "FPS Mineables": "FPS",
    "ROC Mineables": "ROC",
}

# Categories where the piece/rock count isn't meaningful — show the name alone.
NO_COUNT = {"Salvage/Harvestables"}


def _build_table():
    table = []
    for name, base, max_count in ENTRIES:
        for count in range(1, max_count + 1):
            table.append({"name": name, "count": count, "value": base * count})
    return table


TABLE = _build_table()


def matches(value):
    """Every entry tied for the closest signature to `value`, or [] if invalid.

    Normally one entry; for a round-number collision (e.g. 12,000 = Salvage x6
    / FPS x4 / ROC x3) it returns all of them. Each dict has name, count,
    value, delta (entry - input) and abs_delta.
    """
    if value is None or value <= 0:
        return []
    scored = [{**row, "delta": row["value"] - value,
               "abs_delta": abs(row["value"] - value)} for row in TABLE]
    best = min(r["abs_delta"] for r in scored)
    return [r for r in scored if r["abs_delta"] == best]


def best_match(value):
    """The single closest entry, or None for invalid input."""
    cands = matches(value)
    return cands[0] if cands else None


def short_name(name):
    return SHORT_NAMES.get(name, name)


def shows_count(name):
    """False for categories (e.g. Salvage/Harvestables) where count is moot."""
    return name not in NO_COUNT


def label_for(entry):
    """e.g. 'Ice  -  Count: 3', or just the name for no-count categories."""
    if entry is None:
        return ""
    if entry["name"] in NO_COUNT:
        return entry["name"]
    return f"{entry['name']}  -  Count: {entry['count']}"


if __name__ == "__main__":
    # Exact lookups land on the right entry.
    cases = {
        4300: ("Ice", 1), 25800: ("Ice", 6), 12810: ("Iron", 3),
        3200: ("Savrilium", 1), 6400: ("Savrilium", 2),
        25080: ("Hephaestanite", 6), 19500: ("Torite", 5),
        80000: ("ROC Mineables", 20), 60000: ("FPS Mineables", 20),
    }
    ok = True
    for value, (name, count) in cases.items():
        b = best_match(value)
        good = b["name"] == name and b["count"] == count and b["abs_delta"] == 0
        ok = ok and good
        print(f"  [{'OK ' if good else 'FAIL'}] {value:>6} -> {label_for(b)}")

    # Count caps are enforced (Quantainium can't have 3 rocks).
    q3 = any(c["name"] == "Quantainium" for c in matches(9510))
    print(f"  [{'OK ' if not q3 else 'FAIL'}] 9510 is NOT read as Quantainium x3 (cap=2)")
    ok = ok and not q3

    # Salvage/Harvestables shows the name only, no count.
    sh = best_match(2000)
    nc = sh["name"] == "Salvage/Harvestables" and label_for(sh) == "Salvage/Harvestables"
    print(f"  [{'OK ' if nc else 'FAIL'}] 2000 -> {label_for(sh)} (no count shown)")
    ok = ok and nc

    # Round-number collisions list every candidate.
    for value, expected in ((12000, 3), (24000, 3), (6000, 2), (8000, 2)):
        cands = matches(value)
        good = len(cands) == expected and all(c["abs_delta"] == 0 for c in cands)
        ok = ok and good
        names = " / ".join(f"{short_name(c['name'])} x{c['count']}" for c in cands)
        print(f"  [{'OK ' if good else 'FAIL'}] {value} -> {names}")

    print("\nAll checks passed." if ok else "\nSOME CHECKS FAILED.")

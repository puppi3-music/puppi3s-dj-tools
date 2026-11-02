"""Your own genres: training with "everything else" from the scanned collection, the genre switch (starter by
default; both and mine greyed out until there are enough examples), and what it changes and leaves alone.

The "sound" of each track is a made-up embedding put straight into the analysis cache (as a scan would), in
three clear groups: two trained genres and the rest of a house collection. Training reads the cache, so no
audio is decoded and no model is needed."""
from __future__ import annotations

import shutil
import sqlite3

import numpy as np
import pytest
from conftest import TMP as _TMP

from dj_track_analyzer import analysis, collection, mygenres, traktor

TMP = _TMP.resolve()  # training resolves paths (macOS: /var → /private/var), as the cache must see them
DIM = 16
RNG = np.random.default_rng(1)
CENTRES = {"Hyperpop": RNG.normal(0, 1, DIM) * 3, "Pop": RNG.normal(0, 1, DIM) * 3, "House": RNG.normal(0, 1, DIM) * 3}


def sound(kind: str) -> np.ndarray:
    return (CENTRES[kind] + RNG.normal(0, 0.3, DIM)).astype(np.float32)


def add_file(path, emb) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(path.name.encode())  # any bytes: the cache is keyed by path, size and time
    analysis.cache_put(path, 180.0, emb, np.zeros(4, dtype=np.float32))


@pytest.fixture
def setup(monkeypatch):
    """A training folder (Hyperpop, Pop) and a scanned collection: house tracks plus copies of 3 Hyperpop examples."""
    for d in (TMP / "data", TMP / "genres", TMP / "coll"):
        shutil.rmtree(d, ignore_errors=True)
    analysis._genre_bundle.clear()

    def make(n_hyper: int = 15, n_pop: int = 15):
        folder = TMP / "genres"
        copies = []
        for g, n in (("Hyperpop", n_hyper), ("Pop", n_pop)):
            for i in range(n):
                emb = sound(g)
                add_file(folder / g / f"{g} {i}.mp3", emb)
                if g == "Hyperpop" and i < 3:
                    copies.append(emb)
        con = collection.db()
        rows = [(TMP / "coll" / f"House {i}.mp3", sound("House"), "House") for i in range(40)]
        rows += [(TMP / "coll" / f"Copy {i}.flac", e, "IDM") for i, e in enumerate(copies)]  # the same songs, elsewhere
        for p, emb, genre in rows:
            add_file(p, emb)
            con.execute("INSERT INTO tracks (path, filename, type, genre) VALUES (?, ?, 'track', ?)", (str(p), p.name, genre))
        con.commit()
        con.close()
        return folder

    return make


def genres() -> dict[str, tuple]:
    with sqlite3.connect(collection.NATIVE_DB) as con:
        return {r[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]: r[1:] for r in
                con.execute("SELECT path, trained_genre, my_genre, genre, nearest_genre FROM tracks")}


def test_train_gives_your_genre_only_where_it_fits_and_waits_for_the_switch(setup):
    folder = setup()
    report = mygenres.train(str(folder))
    assert report["genres"] == {"Hyperpop": 15, "Pop": 15}
    assert report["everything_else"] == 40  # the copies of examples aren't "everything else"
    assert "not_usable_yet" not in report
    g = genres()
    assert all(g[f"Copy {i}.flac"][0] == "Hyperpop" for i in range(3))
    assert all(g[f"House {i}.mp3"][0] is None for i in range(40))  # none of your genres: keeps ours
    assert all(v[1] is None for v in g.values())  # switch off: nothing uses them yet
    assert all(g[f"House {i}.mp3"][3] in ("Hyperpop", "Pop") for i in range(40))  # the closest of yours, always
    st = mygenres.status()
    assert st["trained"] and st["mode"] == "starter" and not st["in_use"] and st["not_ready"] is None
    assert st["tracks_with_yours"] == 3
    assert "Hyperpop" not in traktor.known_genres()

    prev = mygenres.preview()
    assert prev["changes"] == 3 and prev["by_genre"] == {"Hyperpop": 3}
    assert {t["genre"] for t in prev["tracks"]} == {"IDM"} and {t["would_be"] for t in prev["tracks"]} == {"Hyperpop"}
    assert mygenres.preview("mine")["changes"] == 43  # mine only: every track


def test_switch_starter_both_starter(setup):
    mygenres.train(str(setup()))
    mygenres.set_mode("both")
    g = genres()
    assert all(g[f"Copy {i}.flac"][1] == "Hyperpop" for i in range(3))
    assert all(g[f"House {i}.mp3"][1] is None for i in range(40))
    assert analysis.use_my_genres() and "Hyperpop" in traktor.known_genres()

    mygenres.set_mode("starter")
    assert all(v[1] is None for v in genres().values())
    assert not analysis.use_my_genres() and "Hyperpop" not in traktor.known_genres()


def test_mine_only_gives_every_track_one_of_yours(setup):
    mygenres.train(str(setup()))
    mygenres.set_mode("mine")
    g = genres()
    assert all(g[f"Copy {i}.flac"][1] == "Hyperpop" for i in range(3))
    assert all(g[f"House {i}.mp3"][1] in ("Hyperpop", "Pop") for i in range(40))
    assert mygenres.status()["mode"] == "mine"
    with pytest.raises(ValueError):
        mygenres.set_mode("on")


def test_switch_stays_greyed_out_with_too_few_examples(setup):
    report = mygenres.train(str(setup(n_hyper=12, n_pop=8)))  # 20 in all, Pop under 10
    assert "Needs 30 example tracks, at least 10 in each genre" in report["not_usable_yet"]
    assert "Pop has 8" in report["not_usable_yet"]
    for mode in ("both", "mine"):
        with pytest.raises(SystemExit, match="Needs 30"):
            mygenres.set_mode(mode)
    assert analysis.app_setting("my_genres_mode") is None and mygenres.status()["mode"] == "starter"


def test_a_hand_set_genre_stays(setup):
    mygenres.train(str(setup()))
    house = str(TMP / "coll" / "House 0.mp3")
    collection.set_manual(house, "genre", "Hyperpop")
    for mode in ("both", "mine", "starter"):
        mygenres.set_mode(mode)
        assert genres()["House 0.mp3"][1:3] == ("Hyperpop", "Hyperpop")
    assert house not in {t["path"] for m in ("both", "mine") for t in mygenres.preview(m)["tracks"]}


def test_needs_a_scanned_collection(setup):
    folder = setup()
    with sqlite3.connect(collection.NATIVE_DB) as con:
        con.execute("DELETE FROM tracks WHERE filename LIKE 'House %' AND CAST(substr(filename, 7) AS INT) >= 10")
    with pytest.raises(ValueError, match="Scan your collection first"):
        mygenres.train(str(folder))


def test_older_trained_genres_are_kept_but_not_used(tmp_path, monkeypatch):
    """A database from before the switch: its trained genres were used everywhere; now they wait for the switch."""
    db = tmp_path / "collection.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE tracks (path TEXT PRIMARY KEY, type TEXT, genre TEXT, my_genre TEXT, my_genre_confidence REAL)")
        con.execute("INSERT INTO tracks VALUES ('/m/a.mp3', 'track', 'House', 'Hyperpop', 0.8)")
        # set by hand (✎ Edit puts a hand genre in genre and my_genre): not a trained genre
        con.execute("INSERT INTO tracks VALUES ('/m/b.mp3', 'track', 'Techno', 'Techno', NULL)")
        con.execute("CREATE TABLE manual (path TEXT, field TEXT, value TEXT, was TEXT, at TEXT, PRIMARY KEY (path, field))")
        con.execute("""INSERT INTO manual VALUES ('/m/b.mp3', 'genre', 'Techno', '["House", null]', '')""")
    monkeypatch.setattr(collection, "DB", db)
    con = collection.db()
    rows = {r[0]: tuple(r[1:]) for r in con.execute("SELECT path, my_genre, trained_genre, trained_genre_confidence FROM tracks")}
    con.close()
    assert rows["/m/a.mp3"] == (None, "Hyperpop", 0.8)
    assert rows["/m/b.mp3"] == (None, None, None)  # (apply_manual puts it back in the real, native database)

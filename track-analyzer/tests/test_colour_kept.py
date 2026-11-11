"""The keep-always list: the Colours tab's Forget takes exactly one track off it."""
from __future__ import annotations

from dj_track_analyzer import collection, colors


def test_forget_takes_only_that_track_off():
    a, b = "/music/Kept/Song.flac", "/music/Kept/Song.flac remix.flac"  # b's path contains a's
    collection.NATIVE_DB.parent.mkdir(parents=True, exist_ok=True)  # a fresh home when run alone
    collection.set_hold("colour", {a: "Red", b: "Blue"})
    out = colors.kept(forget_paths=[a])
    assert out["forgotten"] == ["Song.flac"]
    assert [k["path"] for k in out["always_kept"]] == [b]
    assert collection.held("colour") == {b}
    colors.kept(forget_paths=[b])

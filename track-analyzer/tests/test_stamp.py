"""`traktor.stamp_if_unchanged`: file tags Traktor hasn't read block a write; ones it already passed over don't."""
from __future__ import annotations

import mutagen
from dj_track_analyzer import traktor


def entry_for(path, stamp: str) -> str:
    date, secs = stamp.split(" ")
    return (f'<ENTRY MODIFIED_DATE="{date}" MODIFIED_TIME="{secs}" TITLE="{path.stem}" ARTIST="Test">'
            f'<LOCATION DIR="" FILE="{path.name}"></LOCATION><INFO></INFO></ENTRY>')


def test_tagger_comment_blocks_only_while_file_is_newer(library):
    f = library["files"][0]
    tags = mutagen.File(f)
    tags["artist"], tags["comment"] = "Test", "08A - 126 - 7"  # a key tagger's comment Traktor doesn't show
    tags.save()
    y, m, d, s = traktor._stamp_key(traktor.file_stamp(str(f)))
    older, newer = f"{y - 1}/{m}/{d} {s}", f"{y + 1}/{m}/{d} {s}"

    # file changed after Traktor's entry: Traktor should read it first
    assert traktor.stamp_if_unchanged(entry_for(f, older), [str(f)])[1] == "tags_changed"
    # Traktor wrote the entry after the file changed and still has no comment: it won't re-read, so stamp
    entry, how = traktor.stamp_if_unchanged(entry_for(f, newer), [str(f)])
    assert how == "stamped" and traktor.entry_stamp(entry) == traktor.file_stamp(str(f))


def test_stamp_key_orders_by_time():
    assert traktor._stamp_key("2026/11/25 100") > traktor._stamp_key("2026/11/3 86000")

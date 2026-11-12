"""The tests run on a made-up music library and Traktor collection in a temporary home folder, never on yours.

The home folder and the analyzer's data folder are set here, before anything imports dj_track_analyzer (which
reads them at import). The style analysis itself is stood in for (`fake_analysis`): it needs 358 MB of models,
and the Rust/TensorFlow code is checked against Essentia separately (dsp/check.py). Everything around it is real:
finding files, the database, tags, Traktor's paths, the write, the backup, the undo and the Traktor-running check."""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="track-analyzer-tests-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
os.environ["TRACK_ANALYZER_HOME"] = str(TMP / "data")
os.environ["HOME"] = os.environ["USERPROFILE"] = str(TMP / "home")  # USERPROFILE: Windows' ~
for k in ("TRACK_ANALYZER_CONTAINER", "TRACK_ANALYZER_TRAKTOR_CLOSED", "PYTHONUTF8"):
    os.environ.pop(k, None)

WINDOWS = sys.platform == "win32"
TRAKTOR_DIR = TMP / "home" / "Documents" / "Native Instruments" / "Traktor 4.4.2"

# (folder, file name, genres the stand-in analysis gives ("; ": runner-ups close to the top one), colour);
# accents and non-Latin names on purpose
TRACKS = [
    ("Música/House", "Café del Mar - Été (Extended Mix).flac", "Deep House; Progressive House", "Blue"),
    ("Música/House", "東京 Night - ナイト.flac", "Tech House", "Green"),
    ("Música/Trance", "Plain Track.flac", "Trance", "Violet"),
]
RECORDING = ("Recordings", "2026-11-02 Mix.flac")  # Traktor's own mix recording: scans leave it out


def make_audio(path: Path, freq: float, seconds: float = 3.0) -> None:
    """A few seconds of a stereo tone, as FLAC or WAV by the name (PyAV is a dependency anyway)."""
    import av
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 44100
    t = np.arange(int(rate * seconds)) / rate
    tone = (np.sin(2 * np.pi * freq * t) * 12000).astype(np.int16)
    with av.open(str(path), "w") as out:
        stream = out.add_stream("pcm_s16le" if path.suffix == ".wav" else "flac", rate=rate, layout="stereo")
        for i in range(0, len(tone), 4096):
            chunk = tone[i:i + 4096]
            frame = av.AudioFrame.from_ndarray(np.repeat(chunk, 2)[None, :], format="s16", layout="stereo")
            frame.rate, frame.pts = rate, i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def location(path: Path) -> dict:
    """The LOCATION Traktor writes for a file: on Windows VOLUME is the drive letter (seen in a Traktor Pro 4
    collection), on a Mac the disk's name; DIR is "/:"-separated either way."""
    volume = path.drive if WINDOWS else "Macintosh HD"  # "C:"
    return {"DIR": "".join(f"/:{p}" for p in path.parent.parts[1:]) + "/:", "FILE": path.name, "VOLUME": volume,
            "VOLUMEID": "f21b7ddb" if WINDOWS else "Macintosh HD"}


def settings_path(folder: Path) -> str:
    """A folder as Traktor Settings.tsi writes it: plain on Windows (with the last "\\"), colons on a Mac."""
    if WINDOWS:
        return str(folder) + "\\"
    return "Macintosh HD:" + ":".join(folder.parts[1:]) + ":"


def collection_nml(files: list[Path], playlist: list[Path]) -> str:
    entries = []
    for f in files:
        loc = location(f)
        entries.append(
            f'<ENTRY MODIFIED_DATE="2026/11/2" MODIFIED_TIME="40000" LOCK="0" TITLE="{esc(f.stem)}" ARTIST="Test">'
            f'<LOCATION {" ".join(f"{k}={chr(34)}{esc(v)}{chr(34)}" for k, v in loc.items())}></LOCATION>\n'
            f'<MODIFICATION_INFO AUTHOR_TYPE="user"></MODIFICATION_INFO>\n'
            f'<INFO BITRATE="1411000" PLAYTIME="3" PLAYTIME_FLOAT="3.000000" IMPORT_DATE="2026/11/2" FLAGS="12" '
            f'FILESIZE="200"></INFO>\n'
            f'<TEMPO BPM="124.000000" BPM_QUALITY="100.000000"></TEMPO>\n</ENTRY>\n')
    keys = []
    for f in playlist:
        loc = location(f)
        keys.append(f'<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="{esc(loc["VOLUME"] + loc["DIR"] + loc["FILE"])}">'
                    f'</PRIMARYKEY>\n</ENTRY>\n')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
            '<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>\n'
            f'<COLLECTION ENTRIES="{len(files)}">' + "".join(entries) + '</COLLECTION>\n'
            '<SETS ENTRIES="0"></SETS>\n'
            '<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="1">'
            f'<NODE TYPE="PLAYLIST" NAME="Chill"><PLAYLIST ENTRIES="{len(keys)}" TYPE="LIST" UUID="{uuid.uuid4().hex}">'
            + "".join(keys) + '</PLAYLIST>\n</NODE>\n</SUBNODES>\n</NODE>\n</PLAYLISTS>\n'
            '<INDEXING></INDEXING>\n</NML>\n')


def settings_tsi(recordings: Path, samples: Path) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n<NIXML><TraktorSettings>'
            f'<Entry Name="Browser.Dir.Loops" Type="3" Value="{esc(settings_path(samples))}"></Entry>'
            f'<Entry Name="Browser.Dir.Recordings" Type="3" Value="{esc(settings_path(recordings))}"></Entry>'
            '</TraktorSettings></NIXML>\n')


def remove(d: Path) -> None:
    """Delete a folder for the next test. Windows can't delete a file that is still open, and the last test's SQLite
    connections stay open until garbage collection (the cache one is kept open on purpose): close them first. A
    folder that survives would hand the next test the last test's data (a held genre, Rekordbox chosen), so fail."""
    import gc

    from dj_track_analyzer import analysis

    for con in analysis._db_local.__dict__.pop("cons", {}).values():
        con.close()
    gc.collect()
    for _ in range(20):  # a virus scanner may hold a new file for a moment
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            return
        time.sleep(0.25)
    raise RuntimeError(f"{d} could not be deleted (a file in it is still open); the test would see old data")


@pytest.fixture
def library():
    """A fresh music library, Traktor collection and analyzer data folder. Returns paths and the tracks."""
    for d in (TMP / "music", TMP / "data", TRAKTOR_DIR.parent):
        remove(d)
    music = (TMP / "music").resolve()
    files = []
    for i, (folder, name, _genre, _colour) in enumerate(TRACKS):
        f = music / folder / name
        make_audio(f, 220 * (i + 2))
        files.append(f)
    rec = music / RECORDING[0] / RECORDING[1]
    make_audio(rec, 110)
    TRAKTOR_DIR.mkdir(parents=True)
    nml = TRAKTOR_DIR / "collection.nml"
    nml.write_text(collection_nml(files + [rec], files[:2]), encoding="utf-8", newline="\n")
    (TRAKTOR_DIR / "Traktor Settings.tsi").write_text(settings_tsi(rec.parent, music / "Samples"),
                                                      encoding="utf-8", newline="\n")
    return {"music": music, "files": files, "recording": rec, "nml": nml}


@pytest.fixture
def fake_analysis(monkeypatch):
    """Scans give each track the genre and colour in TRACKS instead of running the models."""
    from dj_track_analyzer import analysis

    by_name = {name: (genre, colour) for _f, name, genre, colour in TRACKS}

    def analyze_file(path, timeline=True, genre_bpm=None, **kw):
        genre, colour = by_name.get(Path(path).name, ("House", "Red"))
        return {"type": "track", "duration": "0:03", "bpm": 124.0, "bpm_confidence": 3.0,
                "loudness": {"integrated_lufs": -9.0, "loudness_range_lu": 4.0},
                "dj_genre": [{"genre": g, "score": 0.8 - 0.05 * i} for i, g in enumerate(genre.split("; "))],
                "color": {"color": colour, "position": 0.5, "confidence": 0.9}}

    monkeypatch.setattr(analysis, "analyze_file", analyze_file)
    monkeypatch.setattr(analysis, "models_present", lambda: True)
    monkeypatch.setattr(analysis, "warn_if_maest_missing", lambda: None)


def cli(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """The command line as you'd type it (a separate process; output through a pipe, decoded as UTF-8)."""
    r = subprocess.run([sys.executable, "-m", "dj_track_analyzer", *args], capture_output=True, timeout=600)
    r.stdout, r.stderr = r.stdout.decode("utf-8"), r.stderr.decode("utf-8")
    if check and r.returncode:
        raise AssertionError(f"{' '.join(args)} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


class FakeTraktor:
    """A running program named like Traktor ("Traktor Pro 4.exe" on Windows, as Task Manager shows it)."""

    def __init__(self, folder: Path):
        self.folder, self.procs = folder, []

    def start(self) -> None:
        if WINDOWS:
            exe = self.folder / "Traktor Pro 4.exe"
            shutil.copyfile(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "ping.exe", exe)
            cmd = [str(exe), "-n", "300", "127.0.0.1"]
        else:
            exe = self.folder / "Traktor Pro 4"
            if not exe.exists():  # a link: a copied system binary is killed on macOS (its signature)
                exe.symlink_to(os.path.realpath(shutil.which("sleep")))
            cmd = [str(exe), "300"]
        self.procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self._wait(True)

    def stop(self) -> None:
        for p in self.procs:
            p.kill()
            p.wait()
        self.procs.clear()
        self._wait(False)

    @staticmethod
    def _wait(running: bool) -> None:
        from dj_track_analyzer import traktor

        for _ in range(100):  # until the process list agrees
            if traktor.running_state() is running:
                return
            time.sleep(0.1)
        raise AssertionError(f"the stand-in Traktor {'never showed up in' if running else 'stayed in'} the process list")


_ONE_TRAKTOR = Path(tempfile.gettempdir()) / "dj-tools-tests-fake-traktor.lock"  # shared by all xdist workers


@pytest.fixture(autouse=True)
def _alone_with_fake_traktor(request):
    """The stand-in Traktor is a real process every test can see: while a test runs one (exclusive lock), no other
    test runs, or a parallel worker's write is refused with "Traktor is running"."""
    if sys.platform == "win32":  # no fcntl; the Windows check runs the suite in one process
        yield
        return
    import fcntl

    with open(_ONE_TRAKTOR, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX if "fake_traktor" in request.fixturenames else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


@pytest.fixture
def fake_traktor(tmp_path, _alone_with_fake_traktor):
    t = FakeTraktor(tmp_path)
    yield t
    t.stop()


def make_master_db(folder: Path, files: list[Path], colours: dict[int, int] | None = None, bpm: int | None = None,
                   cues: list[dict] | None = None) -> Path:
    """A made-up Rekordbox master.db (SQLCipher, Rekordbox's own key) with one djmdContent row per file, built from
    pyrekordbox's table definitions. colours: {row index: djmdColor ID}; bpm: djmdContent.BPM (× 100) for every
    row; cues: djmdCue rows as column values (ContentID = row index + 1). Columns Rekordbox fills that these tests
    don't need are left empty, so the schema is built with them nullable."""
    import datetime

    import sqlalchemy as sa
    import sqlcipher3
    from pyrekordbox.masterdb import models
    from pyrekordbox.masterdb.database import BLOB
    from pyrekordbox.utils import deobfuscate
    from sqlalchemy.orm import Session

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "master.db"
    engine = sa.create_engine(f"sqlite+pysqlcipher://:{deobfuscate(BLOB)}@/{path}?", module=sqlcipher3)
    md = sa.MetaData()
    for t in models.Base.metadata.tables.values():
        t.to_metadata(md)
    for t in md.tables.values():
        for c in t.columns:
            c.nullable = not c.primary_key
    md.create_all(engine)
    now = datetime.datetime.now(datetime.timezone.utc)

    def row(model, **kw):
        for col in model.__table__.columns:
            if col.key not in kw and (col.key.endswith("_at") or "date" in col.key):
                kw[col.key] = now
        kw.setdefault("rb_local_usn", 0) if hasattr(model, "rb_local_usn") else None
        return model(**kw)

    with Session(engine) as s:
        s.add(row(models.AgentRegistry, registry_id="localUpdateCount", int_1=100))
        s.add(row(models.DjmdMenuItems, ID="1", Class=1, Name="TRACK"))  # add_content needs both, as a real library has
        s.add(row(models.DjmdDevice, ID="1", MasterDBID="1", Name="this computer"))
        for i, f in enumerate(files):
            s.add(row(models.DjmdContent, ID=str(i + 1), UUID=f"uuid-{i + 1}", FolderPath=str(f), Title=f.stem,
                      Commnt="my note" if i == 0 else "", ColorID=str((colours or {}).get(i, 0)), rb_local_deleted=0, BPM=bpm,
                      SampleRate=44100))
        for k, q in enumerate(cues or []):
            s.add(row(models.DjmdCue, ID=str(900 + k), UUID=f"cue-{k}", rb_local_deleted=0, **q))
        s.commit()
    engine.dispose()
    return path

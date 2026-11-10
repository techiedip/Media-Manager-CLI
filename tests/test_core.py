"""Offline tests: naming, parsing, scanning, planning, apply + undo. No network."""
from pathlib import Path

import pytest

from mediamanager import parsing, planner, scanner
from mediamanager.matching import score
from mediamanager.models import MovieItem, ShowItem
from mediamanager.naming import clean_name, episode_basename, movie_basename, sort_title
from mediamanager.pipeline import normalize_sub_suffix, sidecars


def touch(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ------------------------------------------------------------------ naming
@pytest.mark.parametrize("raw,expected", [
    ("Alien: Covenant", "Alien - Covenant"),
    ("AVP: Alien vs. Predator", "AVP - Alien vs. Predator"),
    ("Happy Progress Day!", "Happy Progress Day!"),
    ("Pretend Like It's the First Time", "Pretend Like It's the First Time"),
    ("What If...?", "What If"),  # trailing dots are unsafe on SMB/Windows
    ("Alien³", "Alien³"),
    ('A/B "test" <x>', "AB test x"),
])
def test_clean_name(raw, expected):
    assert clean_name(raw) == expected


def test_ascii_names():
    assert clean_name("Amélie", ascii_only=True) == "Amelie"


def test_movie_and_episode_names():
    assert movie_basename("A Million Ways to Die in the West", 2014) == "A Million Ways to Die in the West (2014)"
    assert episode_basename("Arcane", 1, [1], ["Welcome to the Playground"]) == \
        "Arcane - S01E01 - Welcome to the Playground"
    assert episode_basename("Breaking Bad", 1, [1, 2], ["Pilot", "Cat's in the Bag..."]) == \
        "Breaking Bad - S01E01E02 - Pilot & Cat's in the Bag"
    assert sort_title("The Matrix") == "matrix"


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("name,season,eps", [
    ("Arcane.S01E01.1080p.WEB.mkv", 1, [1]),
    ("Show.S02E05E06.mkv", 2, [5, 6]),
    ("Show - S03E01-E03.mkv", 3, [1, 2, 3]),
    ("show.1x07.hdtv.avi", 1, [7]),
])
def test_guess_episode(name, season, eps):
    assert parsing.guess_episode(Path(name)) == (season, eps)


def test_bare_episode_number_uses_season_folder():
    assert parsing.guess_episode(Path("05 - Some Title.mkv"), season_hint=2) == (2, [5])


def test_season_dirs():
    assert parsing.season_from_dir("Season 01") == 1
    assert parsing.season_from_dir("S02") == 2
    assert parsing.season_from_dir("Specials") == 0
    assert parsing.season_from_dir("Extras Stuff") is None


def test_guess_movie(tmp_path):
    f = touch(tmp_path / "A.Million.Ways.to.Die.in.the.West.2014.1080p.BluRay.x264-SPARKS.mkv")
    assert parsing.guess_movie(f) == ("A Million Ways to Die in the West", 2014)
    assert parsing.split_title_year("Arcane (2021)") == ("Arcane", 2021)


def test_match_score_prefers_year():
    assert score("The Matrix", 1999, "The Matrix", 1999) > score("The Matrix", 1999, "The Matrix", 2021)


# ------------------------------------------------------------------ scanner
def test_scan_library(tmp_path):
    touch(tmp_path / "Movies" / "Loose.Movie.2010.mkv")
    touch(tmp_path / "Movies" / "Some Film (2001)" / "film.mkv")
    touch(tmp_path / "Movies" / "Some Film (2001)" / "Extras" / "making-of.mkv")
    touch(tmp_path / "Movies" / "Two Parts (1990)" / "two.parts.cd1.avi")
    touch(tmp_path / "Movies" / "Two Parts (1990)" / "two.parts.cd2.avi")
    touch(tmp_path / "TV" / "Show" / "Season 1" / "Show.S01E01.mkv")
    touch(tmp_path / "TV" / "Show" / "Season 1" / "Show.S01E02.mkv")
    touch(tmp_path / "TV" / "Show" / "Specials" / "Show.S00E01.mkv")

    movies = list(scanner.scan(tmp_path / "Movies"))
    assert {type(m) for m in movies} == {MovieItem}
    by_title = {m.guess_title: m for m in movies}
    assert by_title["Loose Movie"].folder is None
    assert by_title["Some Film"].folder.name == "Some Film (2001)"
    assert len(by_title["Some Film"].videos) == 1  # extras ignored
    assert len(by_title["Two Parts"].videos) == 2

    shows = list(scanner.scan(tmp_path / "TV"))
    assert len(shows) == 1 and isinstance(shows[0], ShowItem)
    assert [(e.season, e.episodes) for e in shows[0].episodes] == [(0, [1]), (1, [1]), (1, [2])]


def test_scan_reads_existing_nfo_ids(tmp_path):
    d = tmp_path / "Film (2000)"
    touch(d / "film.mkv")
    (d / "movie.nfo").write_text('<movie><uniqueid type="tmdb">42</uniqueid></movie>')
    (item,) = scanner.scan(d)
    assert item.tmdb_id == 42


# ------------------------------------------------------------------ sidecars
def test_sidecars_do_not_steal_similar_names(tmp_path):
    v = touch(tmp_path / "Alien.mkv")
    touch(tmp_path / "Alien.en.srt")
    touch(tmp_path / "Alien-poster.jpg")
    touch(tmp_path / "Alien - Covenant.srt")
    touch(tmp_path / "Aliens.srt")
    assert [p.name for p in sidecars(v)] == ["Alien-poster.jpg", "Alien.en.srt"]
    assert normalize_sub_suffix(".eng.srt") == ".en.srt"
    assert normalize_sub_suffix(".eng.forced.srt") == ".en.forced.srt"


# ------------------------------------------------------------------ planner
def test_plan_apply_and_undo(tmp_path):
    old = tmp_path / "lib" / "old name"
    video = touch(old / "video.mkv")
    touch(old / "video.en.srt")
    new = tmp_path / "lib" / "New Name (2000)"

    plan = planner.Plan("t")
    assert plan.move(old, new)
    # paths inside the moved dir are visible at their new location before anything is applied
    assert plan.exists(new / "video.mkv")
    assert not plan.exists(old / "video.mkv")
    plan.move(new / "video.mkv", new / "New Name (2000).mkv")
    plan.move(new / "video.en.srt", new / "New Name (2000).en.srt")
    plan.write(new / "movie.nfo", lambda: b"<movie/>")
    plan.fetch(new / "poster.jpg", lambda: b"img")
    assert not plan.fetch(new / "poster.jpg", lambda: b"img2")  # already planned

    j = planner.Journal(tmp_path / "undo")
    errors = []
    planner.apply(plan, j, lambda op, e: errors.append((op, e)))
    assert not errors
    assert sorted(p.name for p in new.iterdir()) == \
        ["New Name (2000).en.srt", "New Name (2000).mkv", "movie.nfo", "poster.jpg"]
    assert not old.exists()

    planner.undo(j.path)
    assert video.exists() and (old / "video.en.srt").exists()
    assert not new.exists()


def test_move_refuses_to_clobber(tmp_path):
    a, b = touch(tmp_path / "a.mkv", b"1"), touch(tmp_path / "b.mkv", b"2")
    plan = planner.Plan("t")
    assert not plan.move(a, b)
    assert plan.warnings


def test_case_only_rename(tmp_path):
    d = tmp_path / "arcane"
    touch(d / "x.mkv")
    plan = planner.Plan("t")
    plan.move(d, tmp_path / "Arcane")
    planner.apply(plan, planner.Journal(tmp_path / "undo"), lambda op, e: (_ for _ in ()).throw(e))
    assert [p.name for p in tmp_path.iterdir() if p.name != "undo"] == ["Arcane"]

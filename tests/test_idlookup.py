"""Parsing IDs/URLs and resolving them with fake providers. No network."""
import pytest

from mediamanager.idlookup import LookupError_, Ref, make_lookup, parse_ref, resolve_movie, resolve_show
from mediamanager.models import SearchResult


@pytest.mark.parametrize("text,ref", [
    ("603", Ref("number", "603")),
    (" tmdb:603 ", Ref("tmdb", "603")),
    ("TMDB 603", Ref("tmdb", "603")),
    ("tmdb:tv:94605", Ref("tmdb_tv", "94605")),
    ("tvdb:371028", Ref("tvdb", "371028")),
    ("tt0133093", Ref("imdb", "tt0133093")),
    ("imdb:tt0133093", Ref("imdb", "tt0133093")),
    ("https://www.themoviedb.org/movie/603-the-matrix", Ref("tmdb", "603")),
    ("https://www.themoviedb.org/movie/603-the-matrix?language=en-US", Ref("tmdb", "603")),
    ("themoviedb.org/tv/94605-arcane", Ref("tmdb_tv", "94605")),
    ("https://thetvdb.com/series/arcane", Ref("tvdb_slug", "arcane")),
    ("https://thetvdb.com/series/the-office-us/seasons/official/1", Ref("tvdb_slug", "the-office-us")),
    ("https://thetvdb.com/series/1923", Ref("tvdb_slug", "1923")),
    ("https://thetvdb.com/dereferrer/series/371028", Ref("tvdb", "371028")),
    ("https://thetvdb.com/?tab=series&id=73244", Ref("tvdb", "73244")),
    ("https://www.imdb.com/title/tt11126994/?ref_=nv_sr", Ref("imdb", "tt11126994")),
    ("https://m.imdb.com/title/tt0133093/", Ref("imdb", "tt0133093")),
    ("<https://www.imdb.com/title/tt0133093/>", Ref("imdb", "tt0133093")),
])
def test_parse_ref(text, ref):
    assert parse_ref(text) == ref


@pytest.mark.parametrize("text", ["", "The Matrix", "tt12", "https://example.com/movie/603"])
def test_parse_ref_rejects(text):
    assert parse_ref(text) is None


class FakeTmdb:
    movies = {603: SearchResult(603, "The Matrix", 1999, "", 1.0)}
    def lookup_movie(self, i): return self.movies.get(i)
    def find_imdb(self, tt): return 603 if tt == "tt0133093" else None
    def find_imdb_tv(self, tt): return 2316 if tt == "tt0386676" else None
    def tv_to_tvdb(self, i): return {94605: 371028, 2316: 73244}.get(i)


class FakeTvdb:
    shows = {371028: SearchResult(371028, "Arcane", 2021, "", 1.0),
             73244: SearchResult(73244, "The Office (US)", 2005, "", 1.0),
             1923: SearchResult(1923, "Some Other Show", 2000, "", 1.0)}
    slugs = {"arcane": 371028, "1923": 4242}
    def lookup_series(self, i): return self.shows.get(i)
    def lookup_slug(self, s):
        return SearchResult(self.slugs[s], s, 2022, "", 1.0) if s in self.slugs else None
    def find_imdb(self, tt): return None  # force the TMDB fallback path


def test_resolve_movie():
    t = FakeTmdb()
    assert resolve_movie(parse_ref("603"), t).title == "The Matrix"
    assert resolve_movie(parse_ref("tt0133093"), t).id == 603
    with pytest.raises(LookupError_, match="no movie with id 9"):
        resolve_movie(parse_ref("9"), t)
    with pytest.raises(LookupError_, match="TV show reference"):
        resolve_movie(parse_ref("tvdb:371028"), t)
    with pytest.raises(LookupError_, match="isn't a movie"):
        resolve_movie(parse_ref("tt0386676"), t)


def test_resolve_show_including_cross_provider():
    tv, tm = FakeTvdb(), FakeTmdb()
    assert resolve_show(parse_ref("371028"), tv, tm).title == "Arcane"
    assert resolve_show(parse_ref("https://thetvdb.com/series/arcane"), tv, tm).id == 371028
    assert resolve_show(parse_ref("tmdb:94605"), tv, tm).id == 371028          # TMDB TV id → TVDB
    assert resolve_show(parse_ref("themoviedb.org/tv/94605"), tv, tm).id == 371028
    assert resolve_show(parse_ref("tt0386676"), tv, tm).id == 73244          # IMDb via TMDB
    assert resolve_show(parse_ref("https://thetvdb.com/series/1923"), tv, tm).id == 4242  # slug wins
    with pytest.raises(LookupError_, match="no TVDB link"):
        resolve_show(parse_ref("tmdb:1"), tv, tm)


def test_make_lookup_explains_unreadable_input():
    with pytest.raises(LookupError_, match="isn't an ID"):
        make_lookup("movie", FakeTmdb(), FakeTvdb())("the matrix")


# ---------------------------------------------------------------- `mm scan --id`
def _lib(tmp_path):
    (tmp_path / "Heat").mkdir()
    (tmp_path / "Heat" / "heat.mkv").write_bytes(b"x")
    (tmp_path / "Ronin").mkdir()
    (tmp_path / "Ronin" / "ronin.mkv").write_bytes(b"x")
    return tmp_path


def test_scan_id_shows_resolved_match(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from mediamanager import cli
    seen = {}

    def fake(item, ref):
        seen["ref"] = ref
        return "Heat (1995) · TMDB 949"
    monkeypatch.setattr(cli, "_resolve_ref", fake)
    r = CliRunner().invoke(cli.app, ["scan", str(_lib(tmp_path) / "Heat"), "--tmdb-id", "949"],
                           env={"COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "ID match" in r.output and "Heat (1995) · TMDB 949" in r.output
    assert seen["ref"] == "tmdb:949"


def test_scan_id_needs_single_item(tmp_path):
    from typer.testing import CliRunner
    from mediamanager import cli
    r = CliRunner().invoke(cli.app, ["scan", str(_lib(tmp_path)), "--id", "949"])
    assert r.exit_code == 2 and "2 were found" in r.output


def test_scan_id_lookup_error(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from mediamanager import cli

    def fake(item, ref):
        raise cli.LookupError_("TMDB has no movie with id 1")
    monkeypatch.setattr(cli, "_resolve_ref", fake)
    r = CliRunner().invoke(cli.app, ["scan", str(_lib(tmp_path) / "Heat"), "--id", "1"])
    assert r.exit_code == 2 and "TMDB has no movie with id 1" in r.output


def test_scan_without_id_stays_offline(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from mediamanager import cli
    monkeypatch.setattr(cli, "_resolve_ref", lambda *a: (_ for _ in ()).throw(AssertionError("network")))
    r = CliRunner().invoke(cli.app, ["scan", str(_lib(tmp_path))])
    assert r.exit_code == 0 and "ID match" not in r.output

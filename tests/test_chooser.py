"""Match chooser: confidence rules, interactive commands, skipped-item report. No network."""
from pathlib import Path

from rich.console import Console

from mediamanager import scanner
from mediamanager.chooser import Chooser, MatchRequest, auto_pick, has_namesake, is_confident, parse_query
from mediamanager.models import SearchResult

LION_1994 = SearchResult(8587, "The Lion King", 1994, "A young lion prince…", 0.91)
LION_2019 = SearchResult(420818, "The Lion King", 2019, "Simba idolizes his father…", 0.91)
OTHER = SearchResult(9732, "The Lion King II", 1998, "", 0.70)


def make(results_by_query=None, answers=(), lookup=None):
    calls = []
    answers = list(answers)

    def search(title, year):
        calls.append((title, year))
        return (results_by_query or {}).get((title, year), [])

    req = MatchRequest("movie", "The Lion King", None, Path("/lib/The Lion King"), search, lookup)
    console = Console(record=True, width=120)
    ch = Chooser(console, "ask", input_fn=lambda prompt: answers.pop(0))
    return ch, req, calls, console


def test_confidence_rules():
    exact = SearchResult(603, "The Matrix", 1999, score=1.0)
    sequel = SearchResult(604, "The Matrix Reloaded", 2003, score=0.6)
    assert is_confident([exact, sequel])
    assert not is_confident([LION_1994, LION_2019])          # remakes tie
    assert not is_confident([SearchResult(1, "x", 2000, score=0.85)])
    assert auto_pick([LION_1994, LION_2019]) is None
    assert auto_pick([SearchResult(1, "x", 2000, score=0.85), SearchResult(2, "y", 2000, score=0.5)]).id == 1
    assert not is_confident([])


def test_no_year_rules():
    arcane = [SearchResult(371028, "Arcane", 2021, score=0.91),
              SearchResult(425860, "Akedo: Ultimate Arcade Warriors", 2021, score=0.4)]
    office = [SearchResult(78107, "The Office", 2001, score=0.91),
              SearchResult(73244, "The Office (US)", 2005, score=0.73)]
    assert is_confident(arcane, year_known=False)           # lone clear title: no prompt
    typo_neighbour = [SearchResult(371028, "Arcane", 2021, score=0.91),
                      SearchResult(467606, "Arcanes", 2025, score=0.84)]
    assert not is_confident(typo_neighbour, year_known=False)
    assert is_confident(typo_neighbour, year_known=False, guess_title="arcane")  # exact title wins
    assert not is_confident(arcane, year_known=True)        # with a year we expect a year match
    assert has_namesake(office) and not is_confident(office, year_known=False, guess_title="The Office")
    assert auto_pick(office, year_known=False) is None
    assert has_namesake([LION_1994, LION_2019])
    assert auto_pick([LION_1994, LION_2019], year_known=False) is None


def test_auto_reason_names_the_problem():
    ch, req, _, console = make()
    ch.mode = "auto"
    ch(req, [LION_1994, LION_2019])
    assert ch.skipped[0].reason == "same title, different versions"


def test_confident_match_needs_no_prompt():
    ch, req, _, _ = make(answers=[])
    assert ch(req, [SearchResult(603, "The Matrix", 1999, score=1.0)]) == 603


def test_pick_by_number():
    ch, req, _, _ = make(answers=["2"])
    assert ch(req, [LION_1994, LION_2019, OTHER]) == 420818


def test_enter_takes_default_when_plausible():
    ch, req, _, _ = make(answers=[""])
    assert ch(req, [LION_1994, LION_2019]) == 8587


def test_enter_skips_when_nothing_plausible():
    ch, req, _, _ = make(answers=[""])
    assert ch(req, [OTHER]) is None
    assert ch.skipped and ch.skipped[0].reason == "skipped by you"


def test_search_again_then_pick():
    hit = SearchResult(420818, "The Lion King", 2019, score=1.0)
    ch, req, calls, _ = make({("The Lion King", 2019): [hit]}, answers=["s The Lion King 2019", "1"])
    assert ch(req, [LION_1994, LION_2019]) == 420818
    assert ("The Lion King", 2019) in calls


def fake_lookup(text):
    from mediamanager.idlookup import LookupError_, parse_ref
    ref = parse_ref(text)
    if ref is None or ref.value != "420818":
        raise LookupError_(f"TMDB has no movie with id {text}")
    return SearchResult(420818, "The Lion King", 2019, "Simba idolizes his father…", 1.0)


def test_id_info_help_and_bad_input():
    ch, req, _, console = make(answers=["?", "i 1", "nonsense", "9", "id 420818", "y"], lookup=fake_lookup)
    assert ch(req, [LION_1994, LION_2019]) == 420818
    out = console.export_text()
    assert "search again" in out and "themoviedb.org/movie/8587" in out and "Didn't understand" in out


def test_quit_stops_asking_for_rest_of_run():
    ch, req, _, _ = make(answers=["q"])
    assert ch(req, [LION_1994, LION_2019]) is None
    assert ch.mode == "auto"
    assert ch(req, [LION_1994, LION_2019]) is None  # no prompt: input_fn would raise IndexError
    assert len(ch.skipped) == 2


def test_confirm_mode_asks_even_when_confident():
    ch, req, _, _ = make(answers=["2"])
    ch.mode = "confirm"
    exact = SearchResult(603, "The Matrix", 1999, score=1.0)
    assert ch(req, [exact, SearchResult(604, "The Matrix Reloaded", 2003, score=0.6)]) == 604


def test_auto_mode_reports_fix_command():
    ch, req, _, console = make()
    ch.mode = "auto"
    assert ch(req, [LION_1994, LION_2019]) is None
    ch.report()
    out = console.export_text()
    assert "--id 8587" in out and "1 item(s) not matched" in out


def test_parse_query():
    assert parse_query("Dune 2021") == ("Dune", 2021)
    assert parse_query("Dune (2021)") == ("Dune", 2021)
    assert parse_query("Blade Runner 2049") == ("Blade Runner 2049", None)
    assert parse_query("Arcane") == ("Arcane", None)


def test_number_in_title_also_searched_in_full():
    ww = SearchResult(464052, "Wonder Woman 1984", 2020, score=1.0)
    ch, req, calls, _ = make({("Wonder Woman 1984", None): [ww]}, answers=["s Wonder Woman 1984", "1"])
    assert ch(req, []) == 464052
    assert calls == [("Wonder Woman", 1984), ("Wonder Woman 1984", None)]


# ------------------------------------------------------------------ --type movie consistency
def _show(root: Path) -> Path:
    for n in (1, 2):
        p = root / f"Season {n}" / f"Show.S0{n}E01.mkv"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
    return root


def test_type_movie_skips_season_folders_directly_and_in_library(tmp_path):
    show = _show(tmp_path / "lib" / "Some Show")
    film = tmp_path / "lib" / "Film (2000)" / "film.mkv"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"x")
    assert list(scanner.scan(show, "movie")) == []
    items = list(scanner.scan(tmp_path / "lib", "movie"))
    assert [i.guess_title for i in items] == ["Film"]
    assert len(list(scanner.scan(show, "auto"))) == 1


# ------------------------------------------------------------------ exact ID lookup in the prompt
def test_id_lookup_shows_title_and_needs_confirmation():
    ch, req, _, console = make(answers=["id 420818", "n", "id 420818", ""], lookup=fake_lookup)
    assert ch(req, [LION_1994, LION_2019]) == 420818   # declined once, then accepted with Enter
    out = console.export_text()
    assert out.count("Found: The Lion King (2019) · TMDB 420818") == 2 and "Not used." in out


def test_pasted_url_and_big_number_are_lookups():
    url = "https://www.themoviedb.org/movie/420818-the-lion-king"
    ch, req, _, _ = make(answers=[url, "y"], lookup=fake_lookup)
    assert ch(req, [LION_1994, LION_2019]) == 420818
    ch, req, _, _ = make(answers=["420818", "y"], lookup=fake_lookup)  # > 8 rows → treated as an id
    assert ch(req, [LION_1994, LION_2019]) == 420818


def test_failed_lookup_keeps_prompting():
    ch, req, _, console = make(answers=["id 999", "2"], lookup=fake_lookup)
    assert ch(req, [LION_1994, LION_2019]) == 420818
    assert "TMDB has no movie with id 999" in console.export_text()


def test_id_lookup_works_with_no_search_results():
    ch, req, _, _ = make(answers=["tt6105098", "y"], lookup=lambda t: SearchResult(420818, "The Lion King", 2019, "", 1.0))
    assert ch(req, []) == 420818

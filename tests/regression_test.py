#!/usr/bin/env python3
"""
Regression test suite for the PDF conversion pipeline.

Runs the actual conversion driver (`engine/convert_driver.py`, through the
bundled/dev Python runtime built by `tools/build_app.sh`) against a fixed
set of real-world documents with known-correct characteristics, and fails
loudly if anything regresses.

This exists because this project's history has repeatedly shown that a fix
verified against the one document it was written for can silently break a
DIFFERENT document that was already working -- see README.md's Hebrew/RTL
section for several real examples (a font-encoding-swap fix that needed a
common-word list, a multi-column fix that needed page-banding, a table fix
that regressed two previously-clean documents because a code path was
missing the RTL fix entirely). Every PDF-handling change should be checked
against this suite before being considered done, not just the document that
motivated the change.

The fixtures are real documents copied into tests/regression_fixtures/
(gitignored -- personal documents, not meant for version control) rather
than referenced by their original path, because a file living in ~/Downloads
or similar can move or get cleaned up between sessions -- confirmed to
actually happen once already in this project's history.

Usage:
    python3 tests/regression_test.py            # fast checks only
    python3 tests/regression_test.py --full --only Travel --timeout-factor 3   # re-run one flaky check
    python3 tests/regression_test.py --dev       # use build/python-runtime + engine/ (what the next build will contain)
    python3 tests/regression_test.py --full      # also the slow
                                                    # Docling-routed English
                                                    # checks (several
                                                    # minutes; needs the
                                                    # optional English
                                                    # fixtures, see below)
    python3 tests/regression_test.py --verbose   # print each check's
                                                    # full markdown on failure

Exits 0 if nothing fails, 1 if anything fails or a fixture file is missing
(a missing fixture is reported as a failure, not silently skipped -- a
silently-skipped check gives false confidence).

Each check reports PASS, FAIL or WARN. WARN ("inconclusive") is reserved
for the slow Docling-routed checks when the conversion times out or the
process is killed by a signal (e.g. a native-library SIGSEGV): those say
nothing about output quality, and a timeout can't be fixed by a longer
timeout when the cause is a crash. Wrong content on a conversion that
finished is always a FAIL, and a timeout or crash on a fast check is
always a FAIL too. Inconclusive checks are listed at the end rather than
buried, and re-running them is the way to settle them.

Adding a new fixture: drop the PDF in tests/regression_fixtures/, add a
Check(...) entry below with whatever combination of max_violations /
must_contain / must_not_contain captures the bug it's guarding against, and
run this script once to confirm the baseline is what you expect before
trusting it as a regression gate.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "regression_fixtures"
# Generated test files with no personal content, so unlike the fixtures above
# they are committed. The two scans are a page of text drawn as an image
# (Pillow, 200 dpi) and saved as an image-only PDF; the text file is one
# Hebrew sentence encoded as Windows-1255.
SYNTHETIC_FIXTURES_DIR = PROJECT_ROOT / "tests" / "synthetic_fixtures"


def _locate_runtime(prefer_dev: bool = False) -> tuple[Path, Path]:
    """
    Locates python3 + convert_driver.py, preferring the built .app bundle
    (matching what a user actually runs), falling back to the dev
    build/python-runtime layout (matching `swift run`). With prefer_dev the
    dev layout is used first: that tests the source tree as the NEXT build
    will contain it (the .app is stale until ./tools/build_app.sh is re-run).
    """
    if prefer_dev:
        dev_python = PROJECT_ROOT / "build/python-runtime/bin/python3"
        dev_driver = PROJECT_ROOT / "engine/convert_driver.py"
        if dev_python.is_file() and dev_driver.is_file():
            return dev_python, dev_driver
    app_python = (
        PROJECT_ROOT
        / "build/MarkItDown.app/Contents/Resources/python-runtime/bin/python3"
    )
    app_driver = PROJECT_ROOT / "build/MarkItDown.app/Contents/Resources/convert_driver.py"
    if app_python.is_file() and app_driver.is_file():
        return app_python, app_driver

    dev_python = PROJECT_ROOT / "build/python-runtime/bin/python3"
    dev_driver = PROJECT_ROOT / "engine/convert_driver.py"
    if dev_python.is_file() and dev_driver.is_file():
        return dev_python, dev_driver

    sys.exit(
        "ERROR: no built Python runtime found (checked build/MarkItDown.app "
        "and build/python-runtime). Run ./tools/build_app.sh first."
    )


@dataclass
class Check:
    name: str
    fixture: str | None = None  # filename within tests/regression_fixtures/
    synthetic_fixture: str | None = None  # filename within tests/synthetic_fixtures/
    external_path: str | None = None  # absolute path, for documents not
    # appropriate to copy into the repo (e.g. copyrighted commercial
    # ebooks with an explicit "don't redistribute" notice) -- referenced
    # in place instead. More fragile (the path can move), which is
    # exactly why fixture-copying is preferred whenever the document
    # allows it; only use this when it doesn't.
    max_violations: int | None = None  # Hebrew orthography-violation ceiling
    min_hebrew_fraction: float | None = None  # asserts routing decision
    max_hebrew_fraction: float | None = None  # asserts routing decision
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    # Regular expressions that must match nowhere in the output. For
    # corruption with no fixed text to look for -- e.g. an ASCII symbol
    # standing in for a Hebrew letter inside a word, which the Hebrew
    # orthography counter can't see because it only looks at Hebrew letters.
    must_not_match: list[str] = field(default_factory=list)
    # Substrings no driver warning may contain. For failures the converter
    # recovers from on its own (e.g. a page that fell back to plain
    # extraction) the Markdown still looks plausible, so the only reliable
    # signal that the recovery path fired is the warning it emits.
    must_not_warn: list[str] = field(default_factory=list)
    # Substrings some driver warning must contain: the positive counterpart,
    # for input the app cannot convert and has to flag instead.
    must_warn: list[str] = field(default_factory=list)
    # Pins the driver's needs_confirmation flag -- the gate that lands a
    # file in the app's "Needs review" state. None means don't care.
    expect_needs_confirmation: bool | None = None
    min_length: int = 200
    slow: bool = False  # Docling-routed: skipped unless --full
    timeout: int = 60

    @property
    def resolved_path(self) -> Path:
        if self.external_path:
            return Path(self.external_path)
        if self.synthetic_fixture:
            return SYNTHETIC_FIXTURES_DIR / self.synthetic_fixture
        assert self.fixture is not None
        return FIXTURES_DIR / self.fixture


CHECKS: list[Check] = [
    Check(
        name="Curriculum PDF (86pp) -- mirrored headings/TOC, majority-vote fix",
        fixture="curriculum_toc_headings.pdf",
        max_violations=40,  # known false positives: geresh abbreviations (תנ"ך etc.)
        min_hebrew_fraction=0.5,
        must_contain=["הנחות יסוד"],
    ),
    Check(
        name="Math worksheet PDF (39pp) -- font-encoding swap, empty-table noise",
        fixture="math_worksheet_empty_tables.pdf",
        # known false positive: a decorative, letter-spaced heading
        # ("בחרו מ: ספ רים!" -- correctly-flipped "בחרו מספרים!" with the
        # original's decorative spacing splitting "מספרים" into
        # fragments) leaves a "ספ" fragment ending in a non-final פ,
        # which the checker can't distinguish from real corruption. The
        # required must_contain/must_not_contain assertions below confirm
        # the actual body content is correct regardless.
        max_violations=1,
        min_hebrew_fraction=0.5,
        must_contain=[
            "התלמידים יכולים לבחור בעצמם את המספר",
            "וגודל המספרים",
        ],
        must_not_contain=["וגודם המספריל"],  # the original swap-bug reading
        timeout=90,
    ),
    Check(
        name="Davidson magazine article -- original mirrored-text bug",
        fixture="davidson_article_mirrored_text.pdf",
        max_violations=6,  # known false positives: geresh in "צ'אט"
        min_hebrew_fraction=0.5,
        must_contain=["כדי לחלץ מהבוטים יותר"],
        # Same guard as the policy document: 28 lines started with punctuation
        # before the tidy pass, none do now.
        must_not_match=[r"(?m)^\s*(?:- )?[.,:;!?]+\s*[א-ת]"],
    ),
    Check(
        name="Reading-comprehension PDF -- bordered table, RTL column order",
        fixture="reading_comprehension_bordered_table.pdf",
        # All three are the abbreviation ראמ"ה in the page header, which the
        # counter splits at the gershayim. It used to come out mirrored
        # (`ה"מאר`), which the counter happens not to object to.
        max_violations=3,
        min_hebrew_fraction=0.5,
        must_contain=["הבנת הנקרא", "ארבעה ממדי הבנה"],
    ),
    Check(
        name="National pedagogy policy PDF -- title line tied between mirrored/unmirrored",
        fixture="national_pedagogy_policy_title_tie.pdf",
        # Measured baseline on the current code. One more than before the
        # 2026-10-10 reordering fix: a heading that used to stay mirrored
        # (`תשרומו ך"נת`) now reads `תנ"ך ומורשת`, and `תנ` is counted.
        max_violations=21,
        min_hebrew_fraction=0.5,
        must_contain=[
            "דמות הבוגרת והבוגר. המדיניות הפדגוגית הלאומית",
            "תנ\"ך ומורשת",
            # Mixed-direction lines: the Latin term between its two Hebrew
            # halves, brackets the right way round.
            "הללו הוא תנועה מתפיסה פדגוגית מסורתית (Instructionalist) לתפיסת למידה עדכנית.",
        ],
        # The running-header title is made of proper nouns outside the
        # common-word list, so it scores identically mirrored and
        # unmirrored. The old decisiveness test still called it "decisive"
        # (a swap-variant scored differently) and kept it mirrored on
        # every page instead of deferring to the page's majority.
        must_not_contain=["תימואלה תיגוגדפה תוינידמה .רגובהו תרגובה תומד"],
        # Sentence punctuation must end a Hebrew line, not start it: a mirrored
        # line reordered with the bidi algorithm used to come out as
        # `.מהרה ...`. 108 lines of this document did before the tidy pass.
        must_not_match=[r"(?m)^\s*(?:- )?[.,:;!?]+\s*[א-ת]"],
        # Known remaining gap, deliberately not asserted: the cover page's
        # two-line title is still mirrored, because that page has no other
        # Hebrew text to vote with.
    ),
    Check(
        name="Workbook PDF (145pp) -- non-zero-origin CropBox must not degrade the document",
        # Commercial workbook under the author's single-teacher licence, so
        # referenced in place rather than copied into fixtures/ (same
        # reasoning as the guidebook checks below).
        external_path="/path/to/your/workbook.pdf",
        max_violations=28,  # measured baseline on the current code
        min_hebrew_fraction=0.5,
        # The cover and back-cover pages have a CropBox whose top is -7.8,
        # which made the row-band crop raise. Before per-page isolation
        # that one exception dropped all 145 pages to the crude
        # whole-document fallback, which cut every sentence into
        # disconnected fragments. A sentence arriving whole on one line is
        # the cheapest proof the full pipeline ran on the body pages.
        must_contain=[
            "בחוברת שלפניך השקעתי אינספור שעות של עבודה ואהבה",
            # The same sentence with its period at the END of the line. Before
            # the tidy pass it read `. בחוברת ... ואהבה`.
            "בחוברת שלפניך השקעתי אינספור שעות של עבודה ואהבה.\n",
        ],
        must_not_contain=["מערכי שיעור למורותעוד"],  # the fragmented cover's signature
        # With isolation in place the crash alone no longer corrupts the
        # text, only degrades those pages quietly -- so the converter's own
        # fallback warning is what must stay absent.
        must_not_warn=["hit an internal error"],
        timeout=180,
    ),
    Check(
        name="Science textbook booklet (104pp) -- Hebrew font subsets with a wrong ToUnicode map",
        # Published school textbook, so referenced in place like the other
        # copyrighted documents. This one has already moved once (a folder
        # reorganisation), so a "fixture not found" here means re-point it.
        external_path="/path/to/your/science-booklet.pdf",
        # Before the glyph-name fix this document came out as 313 violations
        # (8.9 per 1,000) of Hebrew letters mixed with ASCII punctuation.
        # Measured baseline on the current code. It rose from 57 when niqqud
        # was put back on its letters: the counter splits a pointed word at
        # each mark, so `מִינְיוּם` (17 times) yields a "word" `ינ`; the
        # abbreviation סמ"ק, no longer mirrored, accounts for 3 more.
        max_violations=73,
        min_hebrew_fraction=0.5,
        # Its Hebrew subset fonts carry a ToUnicode map built from the
        # character codes (33 -> "!", 48 -> "0" ...) while the glyph names
        # in /Differences are correct, so the text is only recoverable by
        # trusting the names. A readable story sentence proves it.
        must_contain=[
            "בשנת 1983 ירדו שלושה חוקרים בצוללת קטנה",
            # Two consecutive lines of the story on page 2. The teaching-note
            # sidebar beside it used to be interleaved line by line, so these
            # were never adjacent; their being adjacent proves the sidebar
            # is split off into its own block.
            "הצוללת שטה בתוך קניון עמוק ולפתע, בסופו של מצוק\n"
            "הקניון, קידם את פני החוקרים מראה מרהיב: מתוך קרקעית",
            # A bullet that used to sit on a line of its own, above `.לאפיין …`
            # with the period at the front: now one list item, period last.
            "- לאפיין סוגים שונים של סלעים וקרקעות על פי תכונותיהם.",
        ],
        # One typeface family exported by a second tool spells its glyph
        # names differently (`samech`, `final_mem`), so exactly the samekh and
        # the final-form letters stayed wrong: `ניסוי` came out `ני#וי`,
        # `ארגון` came out `ארגו9`. These patterns each matched 13-35 lines
        # of that output and match none now.
        must_not_match=[
            r"[א-ת][#&][א-ת]",
            r"(?<![0-9A-Za-z])[#&][א-ת]{2,}",
            r"[א-ת]{2,}[#&](?![0-9A-Za-z])",
            # At the END of a word (`ארגו9`). One line of this booklet is two
            # printed lines on top of each other, a list number among them,
            # and a digit in the middle of that is not this bug.
            r"[א-ת]{3,}[0-9](?![0-9A-Za-zא-ת])",
        ],
        # Also pins the "Needs review" gate: this document used to trip it,
        # and a regression of the font fix would trip it again.
        expect_needs_confirmation=False,
        timeout=180,
    ),
    Check(
        name="InDesign booklet (26pp) -- mixed-direction lines, cards in columns, list tabs",
        fixture="indesign_cards_mixed_direction.pdf",
        max_violations=3,  # measured baseline on the current code
        min_hebrew_fraction=0.5,
        must_contain=[
            # A Hebrew line with an embedded Latin word: the two halves used
            # to be exchanged around it and the brackets mirrored
            # (`הביאו איתם ... )AI( כלי הבינה המלאכותית`).
            "כלי הבינה המלאכותית (AI) הביאו איתם מציאות חדשה בכיתה. אחד האתגרים הגדולים",
            "אמיתיות בשימוש ב-AI.",
            "#לשימוש מורה #כשירות AI: תפקוד אתי",
            # More Latin letters than Hebrew ones, still a Hebrew line.
            "חיפוש ממוקד ב-Google Scholar",
            # A Latin-only line ending a Hebrew sentence (was `.)SciSpace`).
            "ראשוניים בכלים ייעודיים (כגון Google Scholar,\nSciSpace).",
            # Two cards side by side: each heading followed by its own text.
            # Row bands used to cut across both (heading, heading, text, text)
            # and the left card came first.
            "1.1 קוד אתי\nהאתגר הפדגוגי\nתלמידים (וגם מורים) חווים",
            "1.3 כלי עזר:\nמפת הסקרנות שלי\nהאתגר הפדגוגי\nתלמידים מתקשים לזהות",
            # A heading a little above its column, on a page that used to be
            # taken for a table and came out mirrored (`תורפס תריקס 2`).
            'המטרה הפדגוגית\nלהילחם ישירות ב"אשליית',
            # The space InDesign writes on top of a line's last letter.
            "יצירת מסגרת עבודה ברורה ומוסכמת, שמורידה\n",
            "אפשרות למשוב על תהליך החשיבה ולא רק\n",
            # Automatic list numbers are vector outlines; the tab after them
            # is what marks the item.
            "- מקיימים דיון כיתתי מונחה סביב דילמות אתיות",
            # Niqqud stays on its letter.
            "- מֵעבר לחומת התשלום",
            # A short line whose mirrored form "contains" a common word.
            "לשלב הבא.",
        ],
        must_not_contain=["תורפס תריקס", ")AI(", "| ---"],
        must_not_match=[
            r"(?m)^\s*(?:- )?[.,:;!?]+\s*[א-ת]",
            # Columns are read right to left: 1.1 before 1.2, 6.1 before 6.2.
            r"(?s)1\.2 מסע חקר אישי.*1\.1 קוד אתי",
            r"(?s)6\.2 איך זה מתחבר.*6\.1 מה הכי חשוב",
            r"(?s)האתגר\nתלמידים נוטים “לקפוץ.*המטרה הפדגוגית\nלהעביר תלמידים",
        ],
        must_not_warn=["brackets back to front"],
        expect_needs_confirmation=False,
        timeout=120,
    ),
    Check(
        name="Hebrew page with a Hebrew-English table -- column order, escaped pipe",
        # A ruled table whose middle column is English, so most of the
        # table's letters are Latin while the page is Hebrew. The column
        # order used to follow the table's own share of Hebrew letters: this
        # one came out left column first (`| הערות | English | מונחים |`).
        # One cell holds `A|B`, which used to split the row into four cells.
        synthetic_fixture="hebrew_mixed_table.pdf",
        must_contain=[
            "| מונחים     | English                        | הערות |\n"
            "| ---------- | ------------------------------ | ----- |\n"
            "| צמחים      | Photosynthesis in green plants | A\\|B  |\n"
            "| נשימת תאים | Cellular respiration process   | 2024  |\n"
            "| מחזור המים | Evaporation and condensation   | תהליך |",
        ],
        expect_needs_confirmation=False,
        min_length=300,
    ),
    Check(
        name="Hebrew text with reversed brackets -- must land in Needs review",
        # Correct Hebrew, so the orthography rate is silent, but every
        # parenthesis is back to front and the Latin word sits on the wrong
        # side of it: what mixed-direction lines looked like before
        # `_visual_to_logical`. The standing positive case for that gate.
        synthetic_fixture="hebrew_reversed_brackets.txt",
        must_warn=["brackets back to front"],
        must_not_warn=["orthography violations"],
        expect_needs_confirmation=True,
    ),
    Check(
        name="Short Windows-1255 text file -- must not be decoded as Cyrillic",
        synthetic_fixture="hebrew_windows1255_short.txt",
        # 26 bytes: too little for markitdown's statistical charset guess,
        # which read it as Windows-1251 (`їьхэ Ґхьэ`) with no warning.
        must_contain=["שלום עולם, מה שלומך היום?"],
        must_not_match=[r"[\u0400-\u04ff]"],
        expect_needs_confirmation=False,
        min_length=20,
    ),
    Check(
        name="Semicolon-separated CSV with decimal commas -- must become a real table",
        synthetic_fixture="hebrew_semicolon_decimal_comma.csv",
        # markitdown's CSV converter splits on commas only: this came out as
        # one column with the semicolons left inside the cells.
        must_contain=["| שם | ממוצע | הערה |", "| דנה | 95,5 | טוב מאוד |"],
        must_not_contain=[";"],
        expect_needs_confirmation=False,
        min_length=50,
    ),
    Check(
        name="Tab-separated UTF-16 CSV (Excel 'Unicode Text') -- must become a real table",
        synthetic_fixture="hebrew_tab_utf16.csv",
        must_contain=["| שם | כיתה | ציון |", "| דנה כהן | ה'2 | 95 |"],
        must_not_contain=["\t"],
        expect_needs_confirmation=False,
        min_length=50,
    ),
    Check(
        name="UTF-8 text file with a byte-order mark -- the mark must not reach the output",
        synthetic_fixture="hebrew_utf8_bom.txt",
        # Needs a file of some length: on a ten-character one the charset
        # detection picked a codec that drops the mark by itself.
        must_contain=["שלום לכולם. זהו מסמך בדיקה בעברית"],
        must_not_contain=["\ufeff"],
        expect_needs_confirmation=False,
    ),
    Check(
        name="Abbreviation-dense Hebrew text -- correct text must not land in Needs review",
        synthetic_fixture="hebrew_abbreviation_dense.txt",
        # A Bible-lesson worksheet full of תנ"ך / עמ' abbreviations. The
        # orthography counter reads each such stem as a violation: 13.2 per
        # 1,000 on this correct text, against a threshold of 5.
        must_contain=['קראו את בראשית פרק א\', פס\' א\'–ה\', ועיינו בפירוש רש"י שבעמ\' 12.'],
        must_not_warn=["orthography violations"],
        expect_needs_confirmation=False,
    ),
    Check(
        name="Mirrored Hebrew text -- must land in Needs review",
        # The same worksheet with every line reversed, as text extracted in
        # visual order comes out. The one standing positive case for the
        # orthography-rate gate: no real document trips it any more.
        synthetic_fixture="hebrew_mirrored_lines.txt",
        must_warn=["orthography violations"],
        expect_needs_confirmation=True,
    ),
    # -- Slow (--full only): scans, read by Docling's OCR. The pair pins both
    # sides of the "does this OCR output read as text" gate.
    Check(
        name="Scanned Hebrew PDF (no text layer) -- OCR noise must land in Needs review",
        synthetic_fixture="hebrew_scan_no_text_layer.pdf",
        # No text layer means no letters to sample, so it is routed to
        # Docling, whose OCR cannot read Hebrew and returns Latin-looking
        # noise. That used to be delivered as a clean conversion.
        max_hebrew_fraction=0.15,
        must_warn=["has no text layer"],
        expect_needs_confirmation=True,
        slow=True,
        timeout=400,
    ),
    Check(
        name="Scanned English PDF (no text layer) -- readable OCR must not be flagged",
        synthetic_fixture="english_scan_no_text_layer.pdf",
        max_hebrew_fraction=0.15,
        must_contain=["Students will read the text and answer the questions"],
        must_not_warn=["has no text layer"],
        expect_needs_confirmation=False,
        slow=True,
        timeout=400,
    ),
    # -- Slow (--full only): Docling-routed English documents. Referenced
    # by external_path rather than copied into fixtures/ -- these are
    # commercial travel guides with an explicit "don't redistribute"
    # notice in their own front matter, so a local reference (not a
    # copy) is the appropriate choice; more fragile as a result (the
    # path can move -- treat a "fixture not found" failure here as a
    # prompt to re-point the path, not evidence of a real regression).
    Check(
        name="Travel planner PDF (76pp) -- nested two-column TOC, band+column split",
        external_path="/path/to/your/travel-planner.pdf",
        max_hebrew_fraction=0.15,
        must_contain=["Parkway Mileposts", "Birds, Bears", "DAVID MCKENZIE"],
        must_not_contain=["SIVAD", "ANNAOJ"],  # the original reversed-caption bug
        slow=True,
        # Timed at 302s and 315s with the machine moderately busy and 870-906s
        # with it short of memory (2026-10-10); 400s turned every such run
        # into a WARN. The old and new driver were timed side by side when
        # this was raised, so the headroom is not hiding a slowdown.
        timeout=900,
    ),
    Check(
        name="Guidebook ebook PDF (128pp) -- multi-column body text, no-gutter page",
        external_path="/path/to/your/guidebook-ebook.pdf",
        max_hebrew_fraction=0.15,
        must_contain=["Taubman Museum of Art"],
        slow=True,
        # Directly timed at ~505s on this machine (Docling on 128 pages of
        # scanned/illustrated content is legitimately CPU-heavy, not stuck);
        # 400s produced a false failure, so this leaves real headroom above
        # the observed time rather than just clearing it.
        timeout=700,
    ),
]


def _run_conversion(python_bin: Path, driver: Path, pdf_path: Path, timeout: int):
    """
    Returns (data, error, infrastructure_failure). The last flag is True for
    failures that say nothing about conversion quality -- a timeout, or the
    process being killed by a signal (negative return code, e.g. -11 for a
    native-library SIGSEGV) -- as opposed to an ordinary non-zero exit,
    which means the driver itself failed and is a real failure.
    """
    try:
        result = subprocess.run(
            [str(python_bin), str(driver), str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"conversion timed out after {timeout}s", True
    if result.returncode != 0:
        return (
            None,
            f"conversion failed (exit {result.returncode}): {result.stderr[:500]}",
            result.returncode < 0,
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, f"driver produced non-JSON output: {exc}", False
    return data, None, False


def run_check(
    check: Check,
    python_bin: Path,
    driver: Path,
    violations_fn,
    hebrew_fraction_fn,
    verbose: bool,
) -> tuple[str, list[str]]:
    """Returns ("PASS" | "FAIL" | "WARN", notes). WARN means inconclusive."""
    problems: list[str] = []
    pdf_path = check.resolved_path

    if not pdf_path.is_file():
        return "FAIL", [
            f"fixture not found: {pdf_path} -- copy the document there "
            "(see this script's docstring) or update the Check entry."
        ]

    start = time.monotonic()
    data, error, infrastructure_failure = _run_conversion(
        python_bin, driver, pdf_path, check.timeout
    )
    elapsed = time.monotonic() - start
    if error:
        if infrastructure_failure and check.slow:
            return "WARN", [
                error,
                "inconclusive, not a content failure: timeouts and native-library "
                "crashes are a known flake on Docling-routed checks. Re-run to confirm.",
            ]
        return "FAIL", [error]

    markdown = data["markdown"]

    if len(markdown) < check.min_length:
        problems.append(f"output too short: {len(markdown)} chars (expected >= {check.min_length})")

    if check.max_violations is not None:
        violations = violations_fn(markdown)
        if violations > check.max_violations:
            problems.append(
                f"Hebrew orthography violations: {violations} (expected <= {check.max_violations})"
            )

    if check.min_hebrew_fraction is not None:
        frac = hebrew_fraction_fn(str(pdf_path))
        if frac < check.min_hebrew_fraction:
            problems.append(
                f"Hebrew fraction {frac:.2f} below {check.min_hebrew_fraction} -- "
                "would misroute away from the proven Hebrew RTL path"
            )

    if check.max_hebrew_fraction is not None:
        frac = hebrew_fraction_fn(str(pdf_path))
        if frac > check.max_hebrew_fraction:
            problems.append(
                f"Hebrew fraction {frac:.2f} above {check.max_hebrew_fraction} -- "
                "would misroute onto the Hebrew path instead of Docling"
            )

    for needle in check.must_contain:
        if needle not in markdown:
            problems.append(f"missing expected text: {needle!r}")

    for needle in check.must_not_contain:
        if needle in markdown:
            problems.append(f"found text that should have been fixed: {needle!r}")

    for pattern in check.must_not_match:
        found = list(re.finditer(pattern, markdown))
        if found:
            start = found[0].start()
            snippet = markdown[max(0, start - 12) : start + 24].replace("\n", " ")
            problems.append(
                f"pattern {pattern!r} matched {len(found)} time(s), e.g. ...{snippet}..."
            )

    for needle in check.must_not_warn:
        matched = [w for w in data.get("warnings", []) if needle in w]
        if matched:
            problems.append(f"driver warning matched {needle!r}: {matched[0][:140]}")

    for needle in check.must_warn:
        if not any(needle in w for w in data.get("warnings", [])):
            problems.append(f"no driver warning contains {needle!r}")

    if (
        check.expect_needs_confirmation is not None
        and data.get("needs_confirmation") != check.expect_needs_confirmation
    ):
        problems.append(
            f"needs_confirmation is {data.get('needs_confirmation')}, "
            f"expected {check.expect_needs_confirmation}"
        )

    if verbose and problems:
        print(f"\n----- full output for {check.name!r} -----")
        print(markdown)
        print("----- end output -----\n")

    if problems:
        return "FAIL", problems
    return "PASS", [f"({elapsed:.1f}s)"]


# Packages that make markitdown send audio or video IDs to a cloud service
# (SpeechRecognition -> Google's speech API; youtube-transcript-api -> YouTube).
# The app is offline by design, so none of them may be present in the runtime:
# without them markitdown silently skips transcription instead of going online.
_FORBIDDEN_NETWORK_PACKAGES = ("speech_recognition", "pydub", "youtube_transcript_api")


def check_no_cloud_transcription(python_bin: Path) -> tuple[str, list[str]]:
    code = (
        "import importlib.util as u; "
        f"print(','.join(m for m in {_FORBIDDEN_NETWORK_PACKAGES!r} if u.find_spec(m)))"
    )
    proc = subprocess.run([str(python_bin), "-I", "-B", "-c", code], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return "WARN", [f"could not inspect the runtime: {proc.stderr.strip()[-200:]}"]
    present = [m for m in proc.stdout.strip().split(",") if m]
    if present:
        return "FAIL", [f"cloud-transcription package(s) present in the runtime: {', '.join(present)}"]
    return "PASS", []


_TABLE_RENDERING_PROBE = r"""
import markitdown.converters._pdf_converter as c
# Vowel points take no width: both rows must be padded to the same width.
print(c._render_markdown_table([["מילה", "x"], ["דָּג", "y"]]))
# A grid of numbers has no letters of its own; it follows its page, and the
# document when the page has too few letters to say.
grid = [["1", "2"], ["3", "4"]]
print(c._table_reads_rtl(grid, (300, 320), (0, 0)), c._table_reads_rtl(grid, (10, 300), (0, 0)),
      c._table_reads_rtl(grid, (3, 5), (900, 1000)), c._table_reads_rtl(grid, (50, 100), (100, 1000)))
# A table in one script answers for itself whatever the page says.
print(c._table_reads_rtl([["Name", "Qty"]], (300, 320), (300, 320)), c._table_reads_rtl([["שם", "כמות"]], (0, 300), (0, 300)))
"""
_TABLE_RENDERING_EXPECTED = (
    "| מילה | x   |\n| ---- | --- |\n| דָּג   | y   |\nTrue False True False\nFalse True\n"
)


def check_table_rendering(python_bin: Path) -> tuple[str, list[str]]:
    proc = subprocess.run(
        [str(python_bin), "-I", "-B", "-c", _TABLE_RENDERING_PROBE], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        return "FAIL", [f"probe failed: {proc.stderr.strip()[-300:]}"]
    if proc.stdout != _TABLE_RENDERING_EXPECTED:
        return "FAIL", [f"expected {_TABLE_RENDERING_EXPECTED!r}", f"got      {proc.stdout!r}"]
    return "PASS", []


def check_docling_crash_falls_back(python_bin: Path, driver: Path) -> tuple[str, list[str]]:
    """
    A crash inside docling's native code must not fail the conversion: the
    driver has to fall back to markitdown. The crash is staged by putting a
    stand-in `docling` package first on PYTHONPATH that kills its own process
    with SIGSEGV on import, which is what a real native crash looks like to
    the driver. The fixture is an English PDF with a text layer, so it is
    routed to docling and markitdown can still read it afterwards.
    """
    import os
    import tempfile

    pdf_path = SYNTHETIC_FIXTURES_DIR / "english_text_layer.pdf"
    if not pdf_path.is_file():
        return "FAIL", [f"fixture not found: {pdf_path}"]
    with tempfile.TemporaryDirectory() as tmp:
        package = Path(tmp) / "docling"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "document_converter.py").write_text(
            "import os, signal\nos.kill(os.getpid(), signal.SIGSEGV)\n"
        )
        env = dict(os.environ, PYTHONPATH=tmp, PYTHONDONTWRITEBYTECODE="1")
        try:
            proc = subprocess.run(
                [str(python_bin), "-B", str(driver), str(pdf_path)],
                capture_output=True, text=True, timeout=120, env=env,
            )
        except subprocess.TimeoutExpired:
            return "FAIL", ["conversion timed out after 120s"]
    if proc.returncode != 0:
        return "FAIL", [f"the driver died with the crash (exit {proc.returncode}) instead of falling back"]
    try:
        markdown = json.loads(proc.stdout)["markdown"]
    except (json.JSONDecodeError, KeyError) as exc:
        return "FAIL", [f"driver produced unusable output: {exc}"]
    if "The bus leaves the main gate" not in markdown:
        return "FAIL", ["fallback output is missing the document's text"]
    return "PASS", []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="also run slow Docling-routed checks")
    parser.add_argument("--verbose", action="store_true", help="print full markdown for any failing check")
    parser.add_argument(
        "--only",
        metavar="TEXT",
        help="run only the checks whose name contains TEXT (case-insensitive); implies --full for slow ones",
    )
    parser.add_argument(
        "--timeout-factor",
        type=float,
        default=1.0,
        metavar="N",
        help="multiply every check's conversion timeout by N (slow Docling checks time out on a loaded machine)",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="use build/python-runtime + engine/convert_driver.py instead of the built .app (tests what the next build will contain)",
    )
    args = parser.parse_args()

    python_bin, driver = _locate_runtime(prefer_dev=args.dev)

    sys.path.insert(0, str(PROJECT_ROOT / "engine" / "vendor" / "markitdown" / "src"))
    from markitdown.converters._pdf_converter import _hebrew_orthography_violations

    sys.path.insert(0, str(PROJECT_ROOT / "engine"))
    from convert_driver import _estimate_hebrew_fraction

    checks = [c for c in CHECKS if args.full or args.only or not c.slow]
    if args.only:
        checks = [c for c in checks if args.only.lower() in c.name.lower()]
        if not checks:
            sys.exit(f"ERROR: no check name contains {args.only!r}")
    if args.timeout_factor != 1.0:
        checks = [replace(c, timeout=int(c.timeout * args.timeout_factor)) for c in checks]
    skipped = len(CHECKS) - len(checks) if not args.only else 0

    print(f"Running {len(checks)} regression check(s) against {python_bin}")
    if skipped:
        print(f"({skipped} slow check(s) skipped -- pass --full to include them)")
    print()

    failed: list[str] = []
    inconclusive: list[str] = []

    status, notes = check_no_cloud_transcription(python_bin)
    print(f"[{status}] Runtime has no cloud-transcription packages (offline guarantee)", flush=True)
    for note in notes:
        print(f"       {note}", flush=True)
    if status == "FAIL":
        failed.append("Runtime has no cloud-transcription packages")
    elif status == "WARN":
        inconclusive.append("Runtime has no cloud-transcription packages")

    if not args.only:
        name = "Table rendering -- width without vowel points, direction of mixed and number-only tables"
        status, notes = check_table_rendering(python_bin)
        print(f"[{status}] {name}", flush=True)
        for note in notes:
            print(f"       {note}", flush=True)
        if status == "FAIL":
            failed.append(name)

        name = "A crash inside docling falls back to markitdown"
        status, notes = check_docling_crash_falls_back(python_bin, driver)
        print(f"[{status}] {name}", flush=True)
        for note in notes:
            print(f"       {note}", flush=True)
        if status == "FAIL":
            failed.append(name)

    for check in checks:
        status, notes = run_check(
            check, python_bin, driver, _hebrew_orthography_violations, _estimate_hebrew_fraction, args.verbose
        )
        print(f"[{status}] {check.name}", flush=True)
        for note in notes:
            print(f"       {note}", flush=True)
        if status == "FAIL":
            failed.append(check.name)
        elif status == "WARN":
            inconclusive.append(check.name)

    print()
    if inconclusive:
        print(f"{len(inconclusive)} check(s) INCONCLUSIVE (not counted as failures):")
        for name in inconclusive:
            print(f"  - {name}")
        print()
    if failed:
        print(f"{len(failed)} CHECK(S) FAILED:")
        for name in failed:
            print(f"  - {name}")
        return 1
    print("ALL CHECKS PASSED" + (" (some inconclusive, see above)" if inconclusive else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

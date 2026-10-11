import sys
import io
import re
from typing import BinaryIO, Any

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo
from .._exceptions import MissingDependencyException, MISSING_DEPENDENCY_MESSAGE

# Pattern for MasterFormat-style partial numbering (e.g., ".1", ".2", ".10")
PARTIAL_NUMBERING_PATTERN = re.compile(r"^\.\d+$")

# Hebrew (U+0590-U+05FF) and Arabic (U+0600-U+08FF) Unicode blocks
_RTL_CHAR_PATTERN = re.compile(r"[֐-ࣿ]")

_HEBREW_WORD_PATTERN = re.compile(r"[א-ת]+")
_LAMED_MEM_SWAP_TABLE = str.maketrans({"ל": "ם", "ם": "ל"})

# Private Use Area (Basic Multilingual Plane)
_PUA_PATTERN = re.compile(r"[-]")


def _strip_pua(text: str) -> str:
    """
    Strip Private Use Area characters. PDF icon fonts (commonly
    Wingdings/Symbol-style fonts used for checkboxes, bullets, decorative
    marks) sometimes map their custom glyphs into the PUA range with no
    real Unicode meaning -- text extraction faithfully reproduces the
    codepoint, but it renders as an empty box or nothing in any normal
    font, so it's pure noise in the Markdown output. Worse, it can make
    an all-decorative table cell look "filled" (non-empty string) when
    it's actually empty of real content, which matters for the
    fill-ratio check below.
    """
    if not _PUA_PATTERN.search(text):
        return text
    return _PUA_PATTERN.sub("", text)


def _is_hebrew_dominant(text: str) -> bool:
    """
    True when at least half of `text`'s alphabetic characters are Hebrew.
    Used to decide whether a table's column order should be reversed --
    see `_reverse_rtl_table_columns` -- since Markdown tables always
    render columns in the literal left-to-right order they're written in
    the source, with no renderer reversing that for RTL content.
    """
    if not _RTL_CHAR_PATTERN.search(text):
        return False
    hebrew_chars = sum(len(m.group(0)) for m in _HEBREW_WORD_PATTERN.finditer(text))
    alpha_chars = sum(1 for ch in text if ch.isalpha())
    return bool(alpha_chars) and hebrew_chars / alpha_chars >= 0.5


def _reverse_rtl_table_columns(table_data: list[list[str]]) -> list[list[str]]:
    """
    Reverses each row's cell order when the table's content is
    Hebrew-dominant. Markdown tables always render columns in the literal
    left-to-right order they're written in the source -- no renderer
    reverses column order for RTL content, even though every cell's text
    reads RTL internally -- so the physically-rightmost column (logically
    the *first* one to a Hebrew reader, e.g. a lone bullet-marker column
    that visually belongs on the right) needs to become the first column
    in the emitted Markdown, or the table reads back-to-front even though
    every individual cell's text is correct.
    """
    if not table_data:
        return table_data
    all_cell_text = " ".join(cell or "" for row in table_data for cell in row)
    if not _is_hebrew_dominant(all_cell_text):
        return table_data
    return [list(reversed(row)) for row in table_data]


# Hebrew final letter forms and their regular (non-final) counterparts.
# Final forms are only ever legal as the *last* character of a word;
# regular forms are only ever legal *not* in that position. Counting how
# often a line violates that rule is a cheap, dictionary-free proxy for
# "is this actually correctly-ordered Hebrew" -- mirrored text relocates
# word-final letters to the start/middle of the (now-reversed) word,
# which shows up as a spike in violations.
_FINAL_TO_REGULAR = {"ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ", "ך": "כ"}


def _hebrew_orthography_violations(text: str) -> int:
    violations = 0
    for match in _HEBREW_WORD_PATTERN.finditer(text):
        word = match.group(0)
        last_idx = len(word) - 1
        for i, ch in enumerate(word):
            if ch in _FINAL_TO_REGULAR and i != last_idx:
                violations += 1
            elif (
                len(word) > 1
                and i == last_idx
                and ch in _FINAL_TO_REGULAR.values()
            ):
                violations += 1
    return violations


# The ~150 highest-frequency Hebrew function/short words (pronouns,
# prepositions, conjunctions, question words, common verbs/adverbs).
# Short lines -- especially ones without any word ending in a final
# letter -- give `_hebrew_orthography_violations` no signal either way
# (0 violations in both orientations), so this is a second, independent
# signal: count how many words exactly match this closed, high-frequency
# list. A correctly-ordered Hebrew sentence of any real length almost
# always contains several of these; a mirrored one almost never does,
# since mirroring turns them into other (non-matching) letter sequences.
_HEBREW_COMMON_WORDS = frozenset(
    """
    את של על אל עם כי גם רק לא כן מה מי איך למה מתי איפה כמה
    הוא היא הם הן אני אתה אנחנו אתם אתן זה זאת זו אלה אלו
    יש אין היה הייתה היו יהיה תהיה יכול יכולה יכולים יכולות
    צריך צריכה צריכים רוצה רוצים רוצות אחד אחת שני שתי שלושה שלוש
    כל עוד כבר עדיין תמיד פעם פעמים אחר אחרת אחרים אחרות
    קצת הרבה מאוד יותר פחות גדול קטן טוב רע חדש ישן ראשון אחרון
    בין תחת מעל ליד כאן שם הנה אז כעת עכשיו אחרי לפני
    אבל או אם כאשר כש בגלל כדי לכן למרות אף כמו כפי לפי בלי
    נגד לעומת מול סביב בתוך מחוץ מתוך משהו מישהו כלום אף אחד
    שלו שלה שלהם שלנו שלי שלך לו לה להם לנו לי לך אותו אותה אותם אותנו
    הזה הזאת האלה זהו זוהי דבר דברים אנשים אדם ילד ילדים תלמיד תלמידים
    לעשות לומר לראות לדעת לבוא ללכת לתת לקחת אמר אמרה אמרו אומר אומרת
    אפשר מספר מספרים דרך זמן מקום עבודה שאלה שאלות תשובה תשובות
    דוגמה דוגמאות נושא מילה מילים משפט משפטים טקסט
    """.split()
)


# Hebrew attaches single-letter prefixes (ו"and", ב"in", ל"to", כ"like",
# מ"from", ה"the", ש"that" -- and stacks of them, e.g. "and-the-X")
# directly with no space, so an extracted word token is rarely the bare
# dictionary form. Stripping up to two leading prefix-set letters before
# giving up on a match catches e.g. "המספרים" against the dictionary
# entry "מספרים".
_HEBREW_PREFIX_LETTERS = "ובלכמהש"


def _hebrew_common_word_hits(text: str) -> int:
    hits = 0
    for match in _HEBREW_WORD_PATTERN.finditer(text):
        word = match.group(0)
        if word in _HEBREW_COMMON_WORDS:
            hits += 1
            continue
        for strip_len in (1, 2):
            if len(word) <= strip_len:
                break
            prefix, rest = word[:strip_len], word[strip_len:]
            if all(c in _HEBREW_PREFIX_LETTERS for c in prefix) and rest in _HEBREW_COMMON_WORDS:
                hits += 1
                break
    return hits


def _hebrew_line_score(line: str) -> tuple[int, int]:
    return (_hebrew_orthography_violations(line), -_hebrew_common_word_hits(line))


def _unbalanced_brackets(lines: Any) -> int:
    """Brackets left unmatched when `lines` are read in order as one text."""
    depth = misses = 0
    for line in lines:
        for bracket in _BRACKET_RE.findall(line):
            if bracket in _OPENING_BRACKETS:
                depth += 1
            elif depth:
                depth -= 1
            else:
                misses += 1
    return misses + depth


# -- Visual order to reading order ------------------------------------------
#
# A line read off the page left to right (which is what pdfplumber returns
# for every line, and what InDesign and browsers also write into the content
# stream for Hebrew) is in VISUAL order. Running the Unicode Bidi Algorithm
# over it once more turns it back into reading order, but only if the
# algorithm is told the line's real direction. With a left-to-right base it
# reverses each Hebrew run in place and keeps the runs where they were, so a
# Hebrew sentence with one embedded Latin word or number came out with its
# two halves exchanged: `כלי הבינה המלאכותית (AI) הביאו איתם` was emitted as
# `הביאו איתם )AI( כלי הבינה המלאכותית`, `ב-AI.` as `AI-ב.`, and sentence
# punctuation was stranded at the start of the line. A Hebrew-dominant line
# is therefore resolved with a right-to-left base; a mostly-Latin line with a
# few Hebrew words keeps the left-to-right base.
#
# Brackets need separate care, because the algorithm also MIRRORS them, and
# whether that is right depends on how the PDF's producer recorded them.
# InDesign and Word record the character that was typed (the bracket drawn
# on the visual left of a Hebrew parenthesis is `)`), so reversing the line
# already yields `(...)` and mirroring would break it. Producers that record
# the glyph's shape need the mirroring. The two readings are told apart by
# the brackets themselves: an opening bracket has to come before its closing
# one. `_visual_to_logical` returns the unmirrored reading and
# `_fix_hebrew_extraction` switches a line (or, by majority, a page) to the
# mirrored one when that is the reading whose brackets pair up.
_BRACKET_CHARS = "()[]{}<>«»"
# Stand-ins the algorithm treats exactly like brackets (class ON) but has no
# mirror image for, so a bracket swapped for one comes back unmirrored.
_BRACKET_PLACEHOLDERS = "⌀⌁⌂⌃⌄⌅⌆⌇⌑⌒"
_BRACKETS_TO_PLACEHOLDERS = str.maketrans(_BRACKET_CHARS, _BRACKET_PLACEHOLDERS)
_PLACEHOLDERS_TO_BRACKETS = str.maketrans(_BRACKET_PLACEHOLDERS, _BRACKET_CHARS)
_BRACKET_RE = re.compile(r"[()\[\]{}<>«»]")
_PLACEHOLDER_RE = re.compile("[" + _BRACKET_PLACEHOLDERS + "]")
_OPENING_BRACKETS = "([{<«"

# Bracket-convention evidence from the pages of the document being converted
# so far, as [typed-character votes, glyph-shape votes]. A page with a single
# unpaired bracket has no evidence of its own and borrows the document's.
# Reset at the start of every PDF conversion.
_document_bracket_votes = [0, 0]


# A Latin run at the visual left end of a right-to-left line (the line's
# logical end) with a comma on its RIGHT, before the Hebrew: in a
# right-to-left line a comma that followed the run would sit on its left, so
# this one can only be inside a left-to-right run that carries on to the next
# line (`Google Scholar,` / `SciSpace`). A left-to-right mark after it keeps
# it with the run.
_LATIN_RUN_THEN_PUNCT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 .\-]*[A-Za-z0-9][,;:])(?=\s+\S)")
_LRM = "\u200e"


def _is_rtl_line(line: str, rtl_context: bool) -> bool:
    """
    Whether a line containing Hebrew is a right-to-left line. Mostly-Hebrew
    lines are. On a page that is itself mostly Hebrew the bar is lower, a
    quarter of the letters: `Google Scholar-חיפוש ממוקד ב` has more Latin
    letters than Hebrew ones and is still a Hebrew list item, whereas an
    English sentence quoting one Hebrew word is not.
    """
    if _is_hebrew_dominant(line):
        return True
    if not rtl_context:
        return False
    hebrew = sum(len(m.group(0)) for m in _HEBREW_WORD_PATTERN.finditer(line))
    alpha = sum(1 for ch in line if ch.isalpha())
    return bool(alpha) and hebrew / alpha >= 0.25


def _visual_to_logical(
    line: str,
    mirror_brackets: bool = False,
    rtl_context: bool = False,
    force_rtl: bool = False,
) -> str | None:
    """
    Reading-order text for a line extracted in visual order, or None when the
    bidi library is missing. See the comment block above for the base
    direction and for `mirror_brackets`; `rtl_context` says the surrounding
    page is mostly Hebrew (see `_is_rtl_line`), and `force_rtl` is for a line
    with no Hebrew at all that is known to sit in right-to-left text.
    """
    try:
        from bidi.algorithm import get_display
    except ImportError:
        return None

    if not force_rtl and not _is_rtl_line(line, rtl_context):
        return get_display(line, base_dir="L")
    line = _LATIN_RUN_THEN_PUNCT_RE.sub(lambda m: m.group(1) + _LRM, line)
    if mirror_brackets or not _BRACKET_RE.search(line) or _PLACEHOLDER_RE.search(line):
        return get_display(line, base_dir="R").replace(_LRM, "")
    return (
        get_display(line.translate(_BRACKETS_TO_PLACEHOLDERS), base_dir="R")
        .translate(_PLACEHOLDERS_TO_BRACKETS)
        .replace(_LRM, "")
    )


# Niqqud and cantillation marks. A mark is extracted after the letter it sits
# on, so reversing a line puts it before that letter, where it attaches to
# whatever came before: `מֵעבר` came out with its tsere stranded in front of
# the word. A word that starts with a mark is the sign of it, and then every
# mark in that word is one letter early. Decided word by word: on one real
# line `וּמטבע` was already right while `ְונשים` beside it was not.
_HEBREW_MARKS = "\u0591-\u05bd\u05bf\u05c1\u05c2\u05c4\u05c5\u05c7"
_MARK_INITIAL_WORD_RE = re.compile(
    r"(?<![א-ת" + _HEBREW_MARKS + r"])[" + _HEBREW_MARKS + r"]+[א-ת][א-ת" + _HEBREW_MARKS + r"]*"
)
_MARKS_BEFORE_LETTER_RE = re.compile(r"([" + _HEBREW_MARKS + r"]+)([א-ת])")


def _reattach_hebrew_marks(line: str) -> str:
    return _MARK_INITIAL_WORD_RE.sub(
        lambda m: _MARKS_BEFORE_LETTER_RE.sub(r"\2\1", m.group(0)), line
    )


# A line with no Hebrew in it, inside right-to-left text, that starts with
# closing punctuation: `.)SciSpace` is the end of a Hebrew sentence whose last
# word is Latin, read off the page left to right.
_LATIN_LINE_IN_RTL_RE = re.compile(r"^(?:[.,:;!?]+[()\[\]]?|[)\]])\s*[A-Za-z]")


def _bracket_order(line: str) -> int:
    """
    +1 when the line's first bracket opens and its last one closes, -1 when
    it is the other way round, 0 when there is nothing to judge by (fewer
    than two brackets, or a mix such as a `1)` list marker).
    """
    brackets = _BRACKET_RE.findall(line)
    if len(brackets) < 2:
        return 0
    first_opens = brackets[0] in _OPENING_BRACKETS
    last_opens = brackets[-1] in _OPENING_BRACKETS
    if first_opens and not last_opens:
        return 1
    if last_opens and not first_opens:
        return -1
    return 0


def _hebrew_line_candidates(
    line: str, rtl_context: bool = False
) -> list[tuple[str, bool, bool]]:
    """
    Builds up to four renderings of a single line -- (text, was_flipped,
    was_swapped) -- to handle two independent PDF-extraction bugs that
    can each be present or absent on any given line:

    1. Mirrored (visual-order) text: some PDF producers (e.g. browsers
       rendering "print to PDF" for RTL pages) write Hebrew/Arabic
       glyphs into the content stream in left-to-right visual order
       rather than logical reading order, so pdfminer/pdfplumber
       extract lines mirrored (e.g. "אפשר" becomes "רשפא") while
       embedded numbers/Latin text stay correct. Re-running the Unicode
       Bidi Algorithm (treating the already-visual line as if it needed
       resolving into visual order again) recovers the correct order.
    2. A font-encoding swap: some PDFs embed a subsetted Hebrew font
       whose character encoding has the glyphs for 'ל' (lamed) and 'ם'
       (final mem) mapped to each other's Unicode code points, so every
       extracted 'ל' is actually 'ם' and vice versa (e.g. "לבחור"
       extracts as "םבחור").

    A document can mix both bugs on the very same line, which is why
    every (flip x swap) combination is a candidate rather than deciding
    each independently: deciding order first, from text whose letters
    are still swap-corrupted, can throw off the order decision itself.
    """
    candidates = [(line, False, False)]
    flipped = _visual_to_logical(line, rtl_context=rtl_context)
    if flipped is not None:
        candidates.append((_reattach_hebrew_marks(flipped), True, False))
    candidates += [
        (text.translate(_LAMED_MEM_SWAP_TABLE), flipped, True)
        for text, flipped, _swapped in list(candidates)
    ]
    return candidates


# -- Punctuation and list markers on lines recovered from visual order -------
#
# Reordering a mirrored line with the Unicode Bidi Algorithm puts each Hebrew
# run in the right order but leaves neutral characters where the visual
# layout had them. A sentence's final period sits at the visual LEFT end of a
# right-to-left line, which is the START of the extracted string, so
# `מהרה, ... מוחלטת.` came out as `.מהרה, ... מוחלטת`. Measured on seven real
# documents, 19-49% of Hebrew lines began with sentence punctuation and almost
# none ended with it; the same reordering strands list numbers (`.4 text` for
# `4. text`), puts a page number after its period (`.98 ...שבעמוד` for
# `...שבעמוד 98.`) and detaches bullets from their text.
#
# Applied only to lines whose chosen reading was flipped, because the
# artifact comes from the flip. Each rule was checked on a random sample of
# the lines it rewrites: the first version of the numbering rule turned a page
# reference into a list number in about a third of its matches, which is what
# `_REFERENCE_WORD_RE` is for.
_LEADING_PUNCT_RE = re.compile(r"^(\s*)([.,:;!?]+)\s*(?=[^\s.,:;!?0-9])")
_LEADING_PUNCT_BEFORE_NUMBER_RE = re.compile(r"^(\s*)([,:;!?]+)\s*(?=\d)")
_DOT_NUMBERED_RE = re.compile(r"^(\s*)\.(\d{1,2})\.\s+(?=\S)")  # ".1. text"
_DOT_NUMBER_RE = re.compile(r"^(\s*)\.(\d{1,2})\s+(?=\S)")  # ".12 text"
_REFERENCE_WORD_RE = re.compile(
    r'^(?:ש?ב?עמ(?:וד)?|ה"ש|ב?פרק|ב?סעיף|ב?נספח|ב?טבלה|ב?איור)$'
)
_DOT_LEADER_RE = re.compile(r"(?<=\S)\s*(?:\.\s*){4,}(?=\s*\S)")
_BULLET_GLYPHS = ("•", "●", "▪")
_TRAILING_BULLET_RE = re.compile(r"\s[•●▪]\s*$")
_SPACE_BEFORE_FINAL_PUNCT_RE = re.compile(r"(?<=\S)\s+(?=[.,:;!?]$)")


def _tidy_reordered_hebrew_line(line: str) -> str:
    """
    Moves sentence punctuation that the reordering stranded at the start of a
    Hebrew line to its end, turns `.N text` into `N. text` (or, when the text
    ends in a page/footnote/chapter word, into `text N.`), turns a bullet
    stranded at either end into a Markdown `- ` item, and replaces
    table-of-contents dot leaders with a single "…". Leaves table rows and
    lines that are not mostly Hebrew alone.
    """
    if line.lstrip().startswith("|") or not _is_hebrew_dominant(line):
        return line

    bullet = False
    if line.startswith("\t") and line.strip():
        # InDesign separates an automatic list number or bullet from its item
        # with a tab, and draws the number itself as a vector outline, which
        # leaves no text. The tab (first on the line once it is in reading
        # order) is all that says "list item", so it becomes a Markdown one;
        # the number cannot be recovered and is not guessed.
        line, bullet = line.strip(), True
    elif _TRAILING_BULLET_RE.search(line):
        line, bullet = _TRAILING_BULLET_RE.sub("", line).strip(), True
    elif line.lstrip().startswith(tuple(glyph + " " for glyph in _BULLET_GLYPHS)):
        line, bullet = line.lstrip()[2:], True

    if (m := _LEADING_PUNCT_RE.match(line)) is not None:
        line = line[m.end():].rstrip() + m.group(2)
    elif (m := _DOT_NUMBERED_RE.match(line)) is not None:
        line = m.group(1) + m.group(2) + ". " + line[m.end():].rstrip() + "."
    elif (m := _DOT_NUMBER_RE.match(line)) is not None:
        rest = line[m.end():].rstrip()
        words = rest.split()
        if words and _REFERENCE_WORD_RE.match(words[-1].strip(".,;:")):
            line = m.group(1) + rest + " " + m.group(2) + "."
        else:
            line = m.group(1) + m.group(2) + ". " + rest
    elif (m := _LEADING_PUNCT_BEFORE_NUMBER_RE.match(line)) is not None:
        line = line[m.end():].rstrip() + m.group(2)

    if _DOT_LEADER_RE.search(line):
        line = _DOT_LEADER_RE.sub(" … ", line).strip()
    line = _SPACE_BEFORE_FINAL_PUNCT_RE.sub("", line)
    return "- " + line if bullet else line


def _merge_detached_bullets(lines: list[str]) -> list[str]:
    """
    A bullet that landed on a line of its own (it is a separate text object,
    often in a different row band from its item) is joined to the line right
    after it. Only unambiguous bullet symbols: a lone `-`, `–` or `o` is not
    necessarily a bullet (lone dashes in a math worksheet are not).
    """
    merged: list[str] = []
    i = 0
    while i < len(lines):
        following = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if (
            lines[i].strip() in _BULLET_GLYPHS
            and following
            and not following.startswith("- ")
        ):
            merged.append("- " + following)
            i += 2
        else:
            merged.append(lines[i])
            i += 1
    return merged


def _fix_hebrew_extraction(
    text: str, tidy: bool = False, merge_bullets: bool = False
) -> str:
    """
    Applies the (flip x swap) orientation choice from
    `_hebrew_line_candidates` line by line, scoring each candidate with
    `_hebrew_line_score` (fewer Hebrew orthography violations --  final
    letter forms only ever legally appear as a word's last character --
    tie-broken by matches against a list of common Hebrew function
    words). The original line wins ties, to avoid unnecessary changes.

    Short lines -- headings, table-of-contents entries -- are often too
    short to give either signal anything to go on (no word ends in a
    final letter, and the words involved aren't in the small closed-class
    function-word list), so a per-line decision alone leaves them
    ambiguous and defaults to "no change", which is wrong whenever the
    underlying extraction actually is mirrored. To resolve those, lines
    that *do* get a decisive per-line verdict (in this same call, i.e.
    the same page or paragraph block, which in practice share one
    extraction encoding) vote on flip/swap, and every undecided line
    falls back to that majority -- e.g. a mirrored document-title line
    with no decisive signal of its own still gets flipped correctly
    because the body-text lines around it did have one.

    The per-line decisive check additionally requires that the flip
    question itself -- comparing only the two *unswapped* readings, i.e.
    original vs. flipped -- isn't a tie on its own. Without this, a line
    can be misjudged decisive purely because a swap-corrupted candidate
    happens to score differently, while the real flip choice (original
    vs. flipped, both unswapped) is genuinely ambiguous: a title made of
    distinctive proper nouns (e.g. a document title like "Portrait of the
    Graduate") can score identically unflipped and flipped (zero
    orthography violations either way, neither reading's words common
    enough for the function-word list), yet applying the swap purely by
    coincidence "fixes" one of the two readings' violation count without
    producing real words. Confirmed on a real document title line that
    regressed exactly this way. The fix is scoped narrowly to the
    unswapped-vs-unswapped comparison specifically, rather than
    comparing the best score achievable within each flip value (which
    was tried and caused a real regression elsewhere: a genuinely
    swap-corrupted line's correct flipped reading tied against an
    unrelated line's swap-of-the-wrong-orientation candidate, which
    happened to score identically well without being real Hebrew,
    wrongly marking a clearly-decisive line as ambiguous).

    `tidy` additionally runs `_tidy_reordered_hebrew_line` on the lines that
    were flipped, and `merge_bullets` joins detached bullets across lines.
    Both default to off, and must stay off wherever the returned text feeds a
    layout decision rather than the final output: the tidy pass changes text
    LENGTH (a dot-leader line shrinks from ~90 characters to a few), which
    the form path uses to classify a row as a paragraph, and an earlier
    version that tidied everywhere silently turned plain-text regions of two
    documents into tables. `merge_bullets` also changes the line count, so it
    is only valid on page text, never on one-cell-per-line input.
    """
    if not _RTL_CHAR_PATTERN.search(text):
        return text

    lines = text.split("\n")
    rtl_context = _is_hebrew_dominant(text)
    decided: list[str | None] = []
    pending_candidates: list[list[tuple[str, bool, bool]] | None] = []
    flip_votes: list[bool] = []
    swap_votes: list[bool] = []
    # Whether each line's chosen reading was the flipped one -- only those
    # lines get `_tidy_reordered_hebrew_line`, since the artifact it repairs
    # comes from the flip.
    was_flipped: list[bool] = []
    was_swapped: list[bool] = []

    for line in lines:
        if not _RTL_CHAR_PATTERN.search(line):
            decided.append(line)
            pending_candidates.append(None)
            was_flipped.append(False)
            was_swapped.append(False)
            continue

        candidates = _hebrew_line_candidates(line, rtl_context)
        original_score = _hebrew_line_score(line)
        is_decisive = any(
            _hebrew_line_score(c) != original_score for c, _f, _s in candidates
        )
        if is_decisive:
            unswapped_flipped = next(
                (c for c, f, s in candidates if f and not s), None
            )
            if (
                unswapped_flipped is not None
                and _hebrew_line_score(unswapped_flipped) == original_score
            ):
                is_decisive = False
        if is_decisive:
            best_text, flipped, swapped = min(
                candidates, key=lambda c: _hebrew_line_score(c[0])
            )
            decided.append(best_text)
            was_flipped.append(flipped)
            was_swapped.append(swapped)
            # A verdict resting on a single function-word match, with the
            # orthography count level, is weak: `.אבה בלשל` (mirrored
            # `לשלב הבא.`) "contains" the word `של` once two prefix letters
            # are stripped, and that alone kept it mirrored on a page where
            # every other line was flipped. A weak verdict still votes (on a
            # page of short labels there is nothing else), but gives way to
            # the page's majority when it turns out to disagree with it.
            best_score = _hebrew_line_score(best_text)
            runner_up = min(
                _hebrew_line_score(c)
                for c, f, s in candidates
                if (f, s) != (flipped, swapped)
            )
            if best_score[0] == runner_up[0] and runner_up[1] - best_score[1] <= 1:
                pending_candidates.append(candidates)
            else:
                pending_candidates.append(None)
            flip_votes.append(flipped)
            swap_votes.append(swapped)
        else:
            decided.append(None)
            pending_candidates.append(candidates)
            was_flipped.append(False)
            was_swapped.append(False)

    flip_majority = bool(flip_votes) and sum(flip_votes) > len(flip_votes) / 2
    swap_majority = bool(swap_votes) and sum(swap_votes) > len(swap_votes) / 2

    # A weak verdict (see above) is overruled only by a clear majority; in a
    # block of two or three short cells the "majority" is no better informed
    # than the line itself.
    flips = sum(flip_votes)
    clear_majority = abs(2 * flips - len(flip_votes)) >= 3
    for i, candidates in enumerate(pending_candidates):
        if candidates is None:
            continue
        if decided[i] is not None and not clear_majority:
            continue
        chosen = next(
            (
                (text, flipped)
                for text, flipped, swapped in candidates
                if flipped == flip_majority and swapped == swap_majority
            ),
            None,
        )
        decided[i], was_flipped[i] = chosen if chosen is not None else (lines[i], False)
        was_swapped[i] = chosen is not None and swap_majority

    # Brackets: each flipped line was built unmirrored (see
    # `_visual_to_logical`). Lines whose brackets only pair up in the
    # mirrored reading vote for it; a line with no evidence of its own
    # follows the page, and a page with none follows the document so far.
    mirrored: dict[int, str] = {}
    line_verdicts: dict[int, int] = {}
    for i, flipped in enumerate(was_flipped):
        if not flipped or not _BRACKET_RE.search(decided[i]):  # type: ignore[arg-type]
            continue
        alternative = _visual_to_logical(
            lines[i], mirror_brackets=True, rtl_context=rtl_context
        )
        if alternative is None:
            continue
        alternative = _reattach_hebrew_marks(alternative)
        if alternative == decided[i]:
            continue
        if was_swapped[i]:
            alternative = alternative.translate(_LAMED_MEM_SWAP_TABLE)
        mirrored[i] = alternative
        plain_order = _bracket_order(decided[i])  # type: ignore[arg-type]
        mirrored_order = _bracket_order(alternative)
        if plain_order > 0 and mirrored_order <= 0:
            line_verdicts[i] = 0
        elif mirrored_order > 0 and plain_order <= 0:
            line_verdicts[i] = 1
    # The page as a whole: read in order, brackets must open before they
    # close, across lines too. A parenthesis that wraps (`(כיתות ה'-ו' ...` /
    # `... מותאמת).`) has one bracket on each line and no line to judge by,
    # and one such page turned out to use the other convention from the rest
    # of its document (a back cover made separately).
    page_votes = [
        sum(1 for v in line_verdicts.values() if v == 0),
        sum(1 for v in line_verdicts.values() if v == 1),
    ]
    if mirrored:
        plain_misses = _unbalanced_brackets(decided[i] for i in sorted(mirrored))  # type: ignore[misc]
        mirrored_misses = _unbalanced_brackets(mirrored[i] for i in sorted(mirrored))
        if plain_misses < mirrored_misses:
            page_votes[0] += 1
        elif mirrored_misses < plain_misses:
            page_votes[1] += 1
    _document_bracket_votes[0] += page_votes[0]
    _document_bracket_votes[1] += page_votes[1]
    if page_votes[0] != page_votes[1]:
        page_mirrors = page_votes[1] > page_votes[0]
    else:
        page_mirrors = _document_bracket_votes[1] > _document_bracket_votes[0]
    for i, alternative in mirrored.items():
        if line_verdicts.get(i, int(page_mirrors)) == 1:
            decided[i] = alternative

    # A Latin-only line inside flipped Hebrew text (see
    # `_LATIN_LINE_IN_RTL_RE`), with the page's bracket convention.
    if flip_majority and rtl_context:
        for i, line in enumerate(lines):
            if (
                not _RTL_CHAR_PATTERN.search(line)
                and _LATIN_LINE_IN_RTL_RE.match(line.strip())
            ):
                reordered = _visual_to_logical(
                    line.strip(), mirror_brackets=page_mirrors, force_rtl=True
                )
                if reordered is not None:
                    decided[i] = reordered

    if tidy:
        decided = [
            _tidy_reordered_hebrew_line(line) if flipped else line  # type: ignore[arg-type]
            for line, flipped in zip(decided, was_flipped)
        ]
    if merge_bullets:
        decided = _merge_detached_bullets(decided)  # type: ignore[arg-type]
    return "\n".join(decided)  # type: ignore[arg-type]


def _fix_hebrew_page_text(text: str) -> str:
    """`_fix_hebrew_extraction` for text that goes straight into the output."""
    return _fix_hebrew_extraction(text, tidy=True, merge_bullets=True)


def _merge_partial_numbering_lines(text: str) -> str:
    """
    Post-process extracted text to merge MasterFormat-style partial numbering
    with the following text line.

    MasterFormat documents use partial numbering like:
        .1  The intent of this Request for Proposal...
        .2  Available information relative to...

    Some PDF extractors split these into separate lines:
        .1
        The intent of this Request for Proposal...

    This function merges them back together.
    """
    lines = text.split("\n")
    result_lines: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check if this line is ONLY a partial numbering
        if PARTIAL_NUMBERING_PATTERN.match(stripped):
            # Look for the next non-empty line to merge with
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1

            if j < len(lines):
                # Merge the partial numbering with the next line
                next_line = lines[j].strip()
                result_lines.append(f"{stripped} {next_line}")
                i = j + 1  # Skip past the merged line
            else:
                # No next line to merge with, keep as is
                result_lines.append(line)
                i += 1
        else:
            result_lines.append(line)
            i += 1

    return "\n".join(result_lines)


# Load dependencies
_dependency_exc_info = None
try:
    import pdfminer
    import pdfminer.high_level
    import pdfplumber
except ImportError:
    _dependency_exc_info = sys.exc_info()


# -- Glyph names over a wrong ToUnicode map (Hebrew fonts) -------------------
#
# A simple (Type1/TrueType) PDF font can carry two independent descriptions of
# what each character code means: the glyph *names* in its /Encoding
# /Differences array (e.g. code 33 is "he", code 34 is "nun"), and a separate
# /ToUnicode CMap. pdfminer consults ToUnicode first and only falls back to
# the glyph names for codes ToUnicode doesn't mention at all.
#
# A real Israeli school-textbook PDF shipped Hebrew font subsets whose
# ToUnicode was generated from the character *codes* instead of the glyphs
# (code 33 -> "!", 34 -> '"', 48 -> "0" ...) and omitted about a third of the
# codes entirely, while the glyph names were correct for every code, niqqud
# included. The result was text made of Hebrew letters (the codes ToUnicode
# skipped, which fell back to the names) mixed with ASCII punctuation and
# digits (the codes it got wrong) -- unreadable, and not something any of the
# mirror/swap heuristics below can repair, because the information was gone
# before they ran. OCR can't help either: neither RapidOCR (docling's engine)
# nor macOS Vision recognizes Hebrew. The correct text is simply sitting in
# the PDF, one lookup away.
#
# The rule is deliberately narrow: only for a code whose glyph name resolves
# to a Hebrew character, and only when ToUnicode maps that code to something
# that is NOT Hebrew, the glyph name wins. A ToUnicode that agrees, that maps
# to a different Hebrew character, or that covers non-Hebrew fonts is left
# alone, and fonts with fewer than three Hebrew-named codes are skipped.
_HEBREW_CODEPOINT_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))

# Glyph names Hebrew fonts commonly use that pdfminer's Adobe Glyph List
# lacks, so those codes would otherwise extract as "(cid:N)".
_EXTRA_HEBREW_GLYPH_NAMES = {
    "maqaf": "־",
    "geresh": "׳",
    "gershayim": "״",
    "sofpasuq": "׃",
    "shindot": "ׁ",
}

# Variant spellings seen in a real document's fonts, after underscores are
# removed: the Adobe list says "samekh", and spells the dagesh-tav "tavdages".
_HEBREW_GLYPH_NAME_ALIASES = {
    "samech": "samekh",
    "tavdagesh": "tavdages",
}


def _resolve_hebrew_glyph_name(name: str) -> str | None:
    """
    Resolves a glyph name pdfminer's own lookup could not. A second tool's
    export of the same typeface spelled its names with underscores
    (`final_mem`, `hataf_patah`) and `samech`, so exactly the samekh and the
    final-form letters stayed wrong while every other letter was fixed --
    `ניסוי` came out as `ני#וי`, `ארגון` as `ארגו9`. Only names actually
    observed are aliased; anything else stays unresolved, which leaves its
    code as it was rather than guessing.
    """
    from pdfminer.encodingdb import name2unicode
    from pdfminer.glyphlist import glyphname2unicode

    if name in _EXTRA_HEBREW_GLYPH_NAMES:
        return _EXTRA_HEBREW_GLYPH_NAMES[name]
    try:
        return name2unicode(name)
    except (KeyError, ValueError):
        pass
    # pdfminer reads "_" as a ligature separator ("final_mem" = "final" +
    # "mem"), which is correct per the Adobe spec and not what these fonts
    # mean, so the underscore is dropped to get the one glyph they name.
    normalized = name.replace("_", "")
    normalized = _HEBREW_GLYPH_NAME_ALIASES.get(normalized, normalized)
    return glyphname2unicode.get(normalized) or _EXTRA_HEBREW_GLYPH_NAMES.get(normalized)


def _is_hebrew_char(ch: str) -> bool:
    return len(ch) == 1 and any(lo <= ord(ch) <= hi for lo, hi in _HEBREW_CODEPOINT_RANGES)


def _prefer_hebrew_glyph_names(font: Any, spec: Any) -> None:
    from pdfminer.pdftypes import list_value, resolve1
    from pdfminer.psparser import PSLiteral

    encoding = resolve1(spec.get("Encoding"))
    if not isinstance(encoding, dict):
        return

    names: dict[int, str] = {}
    code = 0
    for item in list_value(encoding.get("Differences", [])):
        item = resolve1(item)
        if isinstance(item, int):
            code = item
        elif isinstance(item, PSLiteral):
            names[code] = item.name
            code += 1

    hebrew_by_code: dict[int, str] = {}
    for code, name in names.items():
        # Decided from the glyph *name*, never from `font.cid2unicode`: that
        # table starts as StandardEncoding and silently keeps its default
        # (code 35 stays "#") for any name pdfminer can't resolve, so asking
        # it first would return the stale default and never reach the resolver.
        ch = _resolve_hebrew_glyph_name(name)
        if ch and _is_hebrew_char(ch):
            hebrew_by_code[code] = ch
    if len(hebrew_by_code) < 3:
        return

    # Assigned, not `setdefault`, for the same reason: the existing entry
    # for an unresolved name is that stale default.
    for code, ch in hebrew_by_code.items():
        font.cid2unicode[code] = ch

    unicode_map = font.unicode_map
    if unicode_map is None:
        return
    for code, ch in hebrew_by_code.items():
        mapped = unicode_map.cid2unichr.get(code)
        if mapped is not None and not all(_is_hebrew_char(c) for c in mapped):
            unicode_map.cid2unichr[code] = ch


def _install_hebrew_glyph_name_priority() -> None:
    """
    Wraps `PDFSimpleFont.__init__` so every simple font pdfminer loads (for
    pdfplumber and for the whole-document pdfminer fallback alike) gets
    `_prefer_hebrew_glyph_names` applied. Idempotent. A failure inside the
    heuristic is swallowed on purpose: it must never be the reason a font,
    and therefore a whole PDF, fails to load.
    """
    from pdfminer import pdffont

    font_class = pdffont.PDFSimpleFont
    if getattr(font_class, "_hebrew_glyph_name_priority", False):
        return
    original_init = font_class.__init__

    def init_with_hebrew_glyph_names(self, descriptor, widths, spec):  # type: ignore[no-untyped-def]
        original_init(self, descriptor, widths, spec)
        try:
            _prefer_hebrew_glyph_names(self, spec)
        except Exception:
            pass

    font_class.__init__ = init_with_hebrew_glyph_names  # type: ignore[method-assign]
    font_class._hebrew_glyph_name_priority = True  # type: ignore[attr-defined]


if _dependency_exc_info is None:
    _install_hebrew_glyph_name_priority()


ACCEPTED_MIME_TYPE_PREFIXES = [
    "application/pdf",
    "application/x-pdf",
]

ACCEPTED_FILE_EXTENSIONS = [".pdf"]


def _to_markdown_table(table: list[list[str]], include_separator: bool = True) -> str:
    """Convert a 2D list (rows/columns) into a nicely aligned Markdown table.

    Args:
        table: 2D list of cell values
        include_separator: If True, include header separator row (standard markdown).
                          If False, output simple pipe-separated rows.
    """
    if not table:
        return ""

    # Normalize None → ""
    table = [[cell if cell is not None else "" for cell in row] for row in table]

    # Filter out empty rows
    table = [row for row in table if any(cell.strip() for cell in row)]

    if not table:
        return ""

    # Column widths
    col_widths = [max(len(str(cell)) for cell in col) for col in zip(*table)]

    def fmt_row(row: list[str]) -> str:
        return (
            "|"
            + "|".join(str(cell).ljust(width) for cell, width in zip(row, col_widths))
            + "|"
        )

    if include_separator:
        header, *rows = table
        md = [fmt_row(header)]
        md.append("|" + "|".join("-" * w for w in col_widths) + "|")
        for row in rows:
            md.append(fmt_row(row))
    else:
        md = [fmt_row(row) for row in table]

    return "\n".join(md)


def _detect_column_gutter(page: Any, min_gutter_pt: float = 8.0) -> tuple[float, float] | None:
    """
    Looks for a single page-spanning vertical gap in character coverage
    -- projecting every character's x-range onto the x-axis regardless
    of its y-position -- that sits roughly in the middle half of the
    page width and is comfortably wider than ordinary word-spacing.

    This is the signature of a two-column magazine/guidebook layout.
    pdfplumber's default text extraction reads primarily top-to-bottom,
    so on a two-column page it interleaves lines from both columns by
    y-position: every individual word extracts correctly, but the
    result reads as complete nonsense (column A's line 1, column B's
    line 1, column A's line 2, ...). Detecting the gutter and reading
    each column separately (see `_extract_plain_page_text`) fixes this.

    Returns (left_column_end_x, right_column_start_x) -- the exact
    bounds of the gap, safe to crop at without clipping any character
    -- if a confident gutter is found, else None. Callers must fall
    back to normal single-pass extraction on None: most pages are a
    single column, and this must not touch them (verified against
    several single-column documents with zero false positives).
    """
    if not page.chars:
        return None

    # Measured within the region itself. `page` is often a crop, and a crop
    # reports absolute page coordinates, so comparing a gap's absolute
    # midpoint with a fraction of the crop's own width rejected every gutter
    # in a crop that does not start at the page's left edge: of three cards
    # side by side, only the first split was ever found, and the other two
    # were interleaved line by line.
    region_x0 = page.bbox[0]
    width = page.bbox[2] - region_x0
    intervals = sorted((c["x0"], c["x1"]) for c in page.chars)
    merged: list[tuple[float, float]] = []
    for x0, x1 in intervals:
        if merged and x0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], x1))
        else:
            merged.append((x0, x1))

    best: tuple[float, float, float] | None = None  # (gap_width, prev_x1, next_x0)
    for (_prev_x0, prev_x1), (next_x0, _next_x1) in zip(merged, merged[1:]):
        gap = next_x0 - prev_x1
        mid = (prev_x1 + next_x0) / 2 - region_x0
        if gap >= min_gutter_pt and width * 0.25 <= mid <= width * 0.75:
            if best is None or gap > best[0]:
                best = (gap, prev_x1, next_x0)

    return (best[1], best[2]) if best else None


_PROSE_WORD_RE = re.compile(r"[^\W\d_]{2,}")


def _column_side_stats(chars: list[dict]) -> tuple[int, float, float, float]:
    """
    (line count, share of lines that are prose, top, bottom) for one side of
    a gutter. A prose line has at least three words of two or more letters:
    digits, lone letters and symbols do not count, so rows of sums, answer
    marks or blanks are never prose however many of them a line holds.
    """
    lines: dict[int, list[dict]] = {}
    for c in chars:
        lines.setdefault(round(c["top"] / 2), []).append(c)
    if not lines:
        return 0, 0.0, 0.0, 0.0
    prose = sum(
        1
        for line in lines.values()
        if len(
            _PROSE_WORD_RE.findall(
                "".join(c["text"] for c in sorted(line, key=lambda c: c["x0"]))
            )
        )
        >= 3
    )
    return (
        len(lines),
        prose / len(lines),
        min(c["top"] for c in chars),
        max(c["bottom"] for c in chars),
    )


def _is_flowing_text_columns(
    region: Any,
    gutter: tuple[float, float],
    min_lines: int = 3,
    min_prose_share: float = 0.5,
    min_height_fraction: float = 0.6,
) -> bool:
    """
    True when both sides of `gutter` hold running text: several lines each,
    at least half of them prose (see `_column_side_stats`), each side
    covering most of the region's height.

    A gutter alone does not say what it separates. Between two columns of
    prose the sides are independent and each must be read top to bottom;
    between the columns of a form or table (label and value, name and
    number) each line belongs with the line beside it. Words per line is
    what tells them apart: prose fills its column, cells hold a word or two.
    The height condition excludes a block that merely sits beside part of
    the text, such as one card under a full-width heading.

    A gutter with a ruling drawn down it is a table's column border, never a
    gap between columns of prose, whatever its cells contain: a curriculum's
    rubric tables have whole paragraphs in every cell. Only thin strokes
    count, and they count a little outside the gap as well, since cell text
    can run up to its border. The edge of a wide filled shape (the card each
    text frame sits on) also falls inside the gutter and says nothing of the
    kind.
    """
    left_end, right_start = gutter
    chars = region.chars
    non_blank = [c for c in chars if c["text"].strip()]
    if not non_blank:
        return False
    height = max(c["bottom"] for c in non_blank) - min(c["top"] for c in non_blank)
    ruled = sum(
        obj["bottom"] - obj["top"]
        for obj in list(region.lines) + list(region.rects)
        if obj["x1"] - obj["x0"] <= 2.0
        and obj["x0"] >= left_end - 10.0
        and obj["x1"] <= right_start + 10.0
    )
    if ruled >= 0.5 * height:
        return False
    for side in (
        [c for c in chars if c["x1"] <= left_end + 0.01],
        [c for c in chars if c["x0"] >= right_start - 0.01],
    ):
        line_count, prose_share, top, bottom = _column_side_stats(side)
        if (
            line_count < min_lines
            or prose_share < min_prose_share
            or bottom - top < min_height_fraction * height
        ):
            return False
    return True


def _page_reads_as_columns(page: Any, bands: list[tuple[float, float]]) -> bool:
    """
    True when the whole page is two columns of running text that must each
    be read to the end before the other -- as opposed to a page whose row
    bands are each to be read across.

    Row bands are found first by default, because a full-width headline
    hides the gutter of the columns below it. That default breaks a page of
    two independent text frames whose paragraphs happen to start at the same
    heights: every shared paragraph gap becomes a band boundary, and the
    page comes out as heading, heading, paragraph, paragraph. So when a
    gutter runs the full height of the page between two sides of running
    text, and each side has at least twice as many lines as the page has
    bands (the bands are paragraphs, not the rows of a grid), the columns
    are split first.
    """
    gutter = _detect_column_gutter(page)
    if gutter is None or not _is_flowing_text_columns(page, gutter):
        return False
    left_end, right_start = gutter
    left_lines = _column_side_stats([c for c in page.chars if c["x1"] <= left_end + 0.01])[0]
    right_lines = _column_side_stats([c for c in page.chars if c["x0"] >= right_start - 0.01])[0]
    return min(left_lines, right_lines) >= 2 * len(bands)


def _join_column_bands(
    page: Any, bands: list[tuple[float, float]], max_gap_pt: float = 24.0
) -> list[tuple[float, float]]:
    """
    Joins consecutive row bands that are really one set of columns: each has
    a gutter in the same place, they are less than `max_gap_pt` apart, and
    the joined region is columns of running text (the same test as
    `_page_reads_as_columns`, within the region).

    This is a column heading sitting a little above its text. The gap under
    a large heading is enough to start a new band, so two cards side by side
    came out as heading, heading, text, text. The distance limit keeps a
    separate block further down the page -- a two-column list under the
    cards, whose gutter happens to line up with theirs -- as a band of its
    own.
    """
    if len(bands) < 2:
        return bands
    x0, _top, x1, _bottom = page.bbox
    info = []
    for top, bottom in bands:
        band = page.crop((x0, top, x1, bottom))
        inked = [c for c in band.chars if c["text"].strip()]
        info.append(
            (
                _detect_column_gutter(band),
                min((c["top"] for c in inked), default=top),
                max((c["bottom"] for c in inked), default=bottom),
            )
        )

    groups: list[list[int]] = [[0]]
    for i in range(1, len(bands)):
        gutter, text_top, _text_bottom = info[i]
        prev_gutter, _prev_top, prev_bottom = info[i - 1]
        if (
            gutter is not None
            and prev_gutter is not None
            and min(gutter[1], prev_gutter[1]) - max(gutter[0], prev_gutter[0]) >= 8.0
            and text_top - prev_bottom < max_gap_pt
        ):
            groups[-1].append(i)
        else:
            groups.append([i])

    joined: list[tuple[float, float]] = []
    for group in groups:
        span = (bands[group[0]][0], bands[group[-1]][1])
        if len(group) > 1:
            region = page.crop((x0, span[0], x1, span[1]))
            if _page_reads_as_columns(region, [bands[i] for i in group]):
                joined.append(span)
                continue
        joined += [bands[i] for i in group]
    return joined


def _has_flowing_text_columns(page: Any) -> bool:
    """
    True when the page, or one of its row bands, is columns of running text.
    Used to keep such a page away from the word-position table heuristic,
    which sees two columns of prose plus a short list as a sparse table and
    emits the two columns' lines side by side in one row.
    """
    bands = _detect_row_bands(page)
    if _page_reads_as_columns(page, bands):
        return True
    if len(bands) < 2:
        return False
    x0, _top, x1, _bottom = page.bbox
    for top, bottom in bands:
        band = page.crop((x0, top, x1, bottom))
        gutter = _detect_column_gutter(band)
        if gutter is not None and _is_flowing_text_columns(band, gutter):
            return True
    return False


# An item that already carries a bullet of its own needs no second one.
_LIST_MARKER_CHARS = "·•●▪◦○■□-–—*"


def _mark_list_tabs(page: Any) -> None:
    """
    Turns the tab that follows an automatic list number or bullet into a
    bullet character, in place, so the item is still recognisable as one.

    InDesign draws automatic numbers and bullets as vector outlines, which
    leave no text behind; the only trace in the text layer is the tab
    between the marker and its item. In a right-to-left list that tab is at
    the item's right-hand end, with the text close on its left and nothing
    close on its right, and only such tabs are converted: a tab between two
    pieces of text is spacing, not a list marker. The numbers themselves are
    not recoverable and are not invented -- every item becomes a plain
    bullet.
    """
    tabs = [c for c in page.chars if c["text"] == "\t"]
    if not tabs:
        return
    letters = [c for c in page.chars if c["text"].strip()]
    for tab in tabs:
        same_line = [
            c for c in letters if c["top"] < tab["bottom"] and c["bottom"] > tab["top"]
        ]
        if not any(_RTL_CHAR_PATTERN.search(c["text"]) for c in same_line):
            continue
        right_gap = min(
            (c["x0"] - tab["x1"] for c in same_line if c["x0"] >= tab["x1"] - 0.5),
            default=float("inf"),
        )
        left_gap = min(
            (tab["x0"] - c["x1"] for c in same_line if c["x1"] <= tab["x0"] + 0.5),
            default=float("inf"),
        )
        nearest_left = max(
            (c for c in same_line if c["x1"] <= tab["x0"] + 0.5),
            key=lambda c: c["x1"],
            default=None,
        )
        if (
            right_gap > 15.0
            and left_gap < 15.0
            and nearest_left is not None
            and nearest_left["text"] not in _LIST_MARKER_CHARS
        ):
            tab["text"] = "•"


def _drop_overlapped_spaces(page: Any) -> Any:
    """
    Returns the page without space characters that sit inside another
    character's box.

    InDesign writes the space that ended a line of right-to-left text (the
    one the line was broken at) on top of the line's last letter, which is
    its leftmost. Read by position, that space lands between the last two
    letters and splits the word: `רק` came out as `ר ק`, `שמורידה` as
    `שמוריד ה`. A space has no extent of its own to protect, so one lying
    inside a letter is not a word gap. "Inside" is 80% of its width: measured
    on six documents, spaces are either under 30% covered (22,000 real ones)
    or over 80% (the stray ones), with almost nothing between. Pages without such a space
    are returned untouched.
    """
    spaces = [c for c in page.chars if c["text"] == " "]
    if not spaces:
        return page
    letters_by_row: dict[int, list[dict]] = {}
    for c in page.chars:
        if c["text"].strip():
            letters_by_row.setdefault(round(c["top"] / 4), []).append(c)
    overlapped: set[tuple[float, float]] = set()
    for space in spaces:
        width = space["x1"] - space["x0"]
        row = round(space["top"] / 4)
        same_line = [
            letter
            for near in range(row - 3, row + 4)
            for letter in letters_by_row.get(near, ())
            if letter["top"] < space["bottom"] and letter["bottom"] > space["top"]
        ]
        if width > 0:
            # By ONE letter. Adding up the letters on both sides was tried
            # and removed real word gaps in a font whose letter boxes are
            # wider than its glyphs (`הבעתעמדהאישית`).
            is_overlapped = any(
                min(space["x1"], c["x1"]) - max(space["x0"], c["x0"]) >= 0.8 * width
                for c in same_line
            )
        else:
            is_overlapped = any(
                c["x0"] - 0.5 <= space["x0"] <= c["x1"] + 0.5 for c in same_line
            )
        if is_overlapped:
            overlapped.add((space["x0"], space["top"]))
    if not overlapped:
        return page
    return page.filter(
        lambda obj: not (
            obj.get("object_type") == "char"
            and obj.get("text") == " "
            and (obj["x0"], obj["top"]) in overlapped
        )
    )


def _detect_sidebar_gutter(
    region: Any,
    min_gap_pt: float = 20.0,
    min_chars: int = 80,
    min_lines: int = 5,
    max_baseline_match: float = 0.5,
    max_narrow_width_fraction: float = 0.4,
) -> tuple[float, float, str] | None:
    """
    Finds a narrow, independently-flowing column sitting off-centre -- a
    sidebar -- that `_detect_column_gutter` cannot see, and returns
    `(left_end_x, right_start_x, narrow_side)` with `narrow_side` being
    "left" or "right", or None.

    `_detect_column_gutter` only accepts a gap whose midpoint is in the
    middle half of the region, because that is where two-column body text
    puts its gutter. A real textbook put a teaching-note sidebar at the far
    left edge, with a clear 29pt gap whose midpoint sat at 23% of the page
    width -- two points outside the window -- so its lines were interleaved
    one by one with the story beside it.

    Simply widening that window would be wrong: an off-centre gap is also
    what a label/value layout or a table of contents looks like, and
    splitting those would separate every label from its value. What tells
    the two apart is baselines. In a label/value layout both sides share
    them, because each label sits on the same line as its value. A sidebar
    has its own font size and leading, so its lines mostly don't line up
    with the main text. The conditions, all required:

    - a gap of at least `min_gap_pt`, well above word spacing, with its
      midpoint within 10-90% of the region (the middle-half case is left
      to `_detect_column_gutter`);
    - a narrow side holding at least `min_chars` characters on at least
      `min_lines` distinct lines, no wider than `max_narrow_width_fraction`
      of the region -- so a page number or a one-line margin note never
      qualifies;
    - fewer than `max_baseline_match` of the narrow side's lines sharing a
      baseline (within 1.5pt) with a line on the other side.

    Checked against every Hebrew document in this project's regression set
    (about 320 pages) it never fires; in the textbook that motivated it, it
    fires on four pages, all genuine sidebars.
    """
    chars = region.chars
    if len(chars) < 2 * min_chars:
        return None

    region_x0, _top, region_x1, _bottom = region.bbox
    width = region_x1 - region_x0
    merged: list[tuple[float, float]] = []
    for x0, x1 in sorted((c["x0"], c["x1"]) for c in chars):
        if merged and x0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], x1))
        else:
            merged.append((x0, x1))

    best: tuple[float, float, float, str] | None = None  # (gap, prev_x1, next_x0, side)
    for (_prev_x0, prev_x1), (next_x0, _next_x1) in zip(merged, merged[1:]):
        gap = next_x0 - prev_x1
        mid = (prev_x1 + next_x0) / 2 - region_x0
        if gap < min_gap_pt or not (0.10 * width <= mid <= 0.90 * width):
            continue
        left = [c for c in chars if c["x1"] <= prev_x1 + 0.01]
        right = [c for c in chars if c["x0"] >= next_x0 - 0.01]
        if len(left) <= len(right):
            narrow, wide, side = left, right, "left"
        else:
            narrow, wide, side = right, left, "right"
        if len(narrow) < min_chars:
            continue
        narrow_width = max(c["x1"] for c in narrow) - min(c["x0"] for c in narrow)
        if narrow_width > max_narrow_width_fraction * width:
            continue
        narrow_lines = sorted({round(c["top"]) for c in narrow})
        if len(narrow_lines) < min_lines:
            continue
        wide_tops = sorted({round(c["top"], 1) for c in wide})
        shared = sum(
            1 for t in narrow_lines if any(abs(t - w) <= 1.5 for w in wide_tops)
        )
        if shared / len(narrow_lines) >= max_baseline_match:
            continue
        if best is None or gap > best[0]:
            best = (gap, prev_x1, next_x0, side)

    return (best[1], best[2], best[3]) if best else None


def _detect_row_bands(page: Any, min_gap_pt: float = 12.0) -> list[tuple[float, float]]:
    """
    Splits a page into horizontal bands by finding page-width-spanning
    vertical gaps -- projecting every character's y-range onto the
    y-axis regardless of x-position -- wider than ordinary line-spacing.
    This is the y-axis counterpart to `_detect_column_gutter`, and
    exists for the same reason: a page can mix layouts vertically (e.g.
    a full-width headline above a two-column body, or a two-column body
    interrupted by a full-width photo caption). Column-gutter detection
    that looks at the *whole* page at once fails on such pages, because
    a single full-width row anywhere is enough to defeat any gutter
    search spanning the full page height even though a clear gutter
    exists within, say, just the body region below the headline.

    Returns (top, bottom) y-ranges covering the whole page top-to-bottom
    (always at least one band, the whole page, when no qualifying gap
    is found or the page has no characters).

    Uses `page.bbox` rather than assuming the page's valid area starts
    at y=0 and ends at `page.height` -- a real document had a cover page
    whose CropBox origin sat above 0 (bbox top -7.8), where `page.height`
    (derived from the box's own height, 842.25) was numerically fine but
    a crop built from literal 0/page.height overshot the actual bottom
    edge (834.42) by that same offset, raising pdfplumber's
    out-of-bounds ValueError. That exception propagated out of the
    per-page loop in the caller and silently downgraded the *entire*
    document (all 145 pages, not just the one cover page) to the crude
    whole-document pdfminer fallback -- confirmed as the actual cause of
    a real document converting badly throughout, not a Hebrew-fix issue.
    """
    page_top, page_bottom = page.bbox[1], page.bbox[3]
    if not page.chars:
        return [(page_top, page_bottom)]

    intervals = sorted((c["top"], c["bottom"]) for c in page.chars)
    merged: list[tuple[float, float]] = []
    for top, bottom in intervals:
        if merged and top <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], bottom))
        else:
            merged.append((top, bottom))

    bands: list[tuple[float, float]] = []
    band_start = page_top
    for (_top0, bottom0), (top1, _bottom1) in zip(merged, merged[1:]):
        gap = top1 - bottom0
        if gap >= min_gap_pt:
            # Clamped to the page: text positioned below (or above) the page
            # edge makes the midpoint of a gap land off the page, and
            # pdfplumber refuses to crop outside it. That happened on 27 of
            # a 76-page travel planner's pages, each silently degrading to
            # plain extraction when it raised. A gap that clamps to nothing
            # is dropped, not turned into an empty band.
            band_end = min(max((bottom0 + top1) / 2, page_top), page_bottom)
            if band_end > band_start:
                bands.append((band_start, band_end))
                band_start = band_end
    if page_bottom > band_start or not bands:
        bands.append((band_start, page_bottom))
    return bands


def _extract_region_text(region: Any, depth: int = 0, max_depth: int = 2) -> str:
    """
    Extracts one region's (a page, or a crop of one) text, recursively
    splitting into columns wherever `_detect_column_gutter` finds a
    confident gutter within that region -- so a column that itself
    contains a further column split (seen in practice: a three-column
    page where the widest gutter only separates column 1 from columns
    2+3) still gets fully separated, not just the single widest split.
    `max_depth` bounds the recursion (2 levels = up to 4 columns) to
    avoid pathological over-splitting on noisy gutter detections.

    Each column is read via pdfplumber's own `extract_text()` on the
    cropped region -- reusing its line-grouping rather than
    reimplementing it -- and columns are joined in reading order:
    left-to-right, or right-to-left when the region's text is mostly Hebrew.

    A sidebar (`_detect_sidebar_gutter`) is split off only when there is
    no ordinary column gutter, and is the one exception to left-to-right:
    a sidebar is an aside to the main text, so the main column is emitted
    first and the sidebar after it, whichever side it sits on.
    """
    gutter = _detect_column_gutter(region) if depth < max_depth else None
    sidebar = _detect_sidebar_gutter(region) if gutter is None and depth < max_depth else None
    if gutter is None and sidebar is None:
        return (region.extract_text() or "").strip()

    narrow_side = None
    if gutter is not None:
        left_end, right_start = gutter
    else:
        left_end, right_start, narrow_side = sidebar  # type: ignore[misc]

    # `.bbox` is (x0, top, x1, bottom) in absolute page coordinates for
    # both a Page and a CroppedPage, and pdfplumber's `.crop()` always
    # expects absolute page coordinates too (even when called on an
    # already-cropped region) -- verified empirically, since getting
    # this wrong would silently crop the wrong area rather than error.
    x0, top, x1, bottom = region.bbox
    left = region.crop((x0, top, left_end, bottom))
    right = region.crop((right_start, top, x1, bottom))
    left_text = _extract_region_text(left, depth + 1, max_depth)
    right_text = _extract_region_text(right, depth + 1, max_depth)
    # Columns are read in the direction of the text: right column first on a
    # Hebrew page. A left sidebar also comes after the main text.
    if narrow_side == "left" or (
        narrow_side is None
        and _is_hebrew_dominant("".join(c["text"] for c in region.chars))
    ):
        return "\n\n".join(t for t in (right_text, left_text) if t)
    return "\n\n".join(t for t in (left_text, right_text) if t)


def _extract_banded_region_text(region: Any) -> str:
    """
    `_extract_region_text` for a region that may mix layouts vertically,
    used for the text around a page's tables. That path never split a
    region into row bands, so a full-width element anywhere in it (the
    textbook's pink question banner, spanning the whole card) filled the
    gap beside a sidebar and hid it. Bands are used only when some band
    actually contains a sidebar; otherwise this is exactly
    `_extract_region_text`, so no page without a sidebar changes.
    """
    bands = _detect_row_bands(region)
    if len(bands) > 1:
        x0, _top, x1, _bottom = region.bbox
        band_regions = [region.crop((x0, t, x1, b)) for t, b in bands]
        if any(_detect_sidebar_gutter(r) is not None for r in band_regions):
            return "\n\n".join(
                text for r in band_regions if (text := _extract_region_text(r))
            )
    return _extract_region_text(region)


def _extract_plain_page_text(page: Any) -> str | None:
    """
    Extracts a page's text, first splitting it into horizontal bands
    (`_detect_row_bands`) to isolate regions with a consistent layout,
    then within each band recursively splitting into columns wherever a
    confident gutter is found (`_extract_region_text`). Both steps fall
    back to a single whole-page pass via plain `page.extract_text()`
    when no band/column signal is found at all, which is the common
    single-column case and the large majority of pages even in
    multi-column documents (covers, section dividers, and other
    non-body-text pages are usually single-column) -- verified to
    produce byte-identical output to the pre-existing behavior on such
    pages before trusting this on real multi-column content.

    Two exceptions to "bands first", both for columns of running text
    whose paragraphs line up across the gutter: a page that is such
    columns from top to bottom is split into columns straight away
    (`_page_reads_as_columns`), and a heading band is joined to the
    column band right under it (`_join_column_bands`).
    """
    bands = _detect_row_bands(page)
    if (
        len(bands) == 1
        and _detect_column_gutter(page) is None
        and _detect_sidebar_gutter(page) is None
    ):
        # No band signal, no column signal: identical to prior behavior.
        return page.extract_text()

    if len(bands) > 1 and _page_reads_as_columns(page, bands):
        return _extract_region_text(page) or None
    bands = _join_column_bands(page, bands)

    page_x0, _page_top, page_x1, _page_bottom = page.bbox
    band_texts = [
        text
        for top, bottom in bands
        if (
            text := _extract_region_text(
                page.crop((page_x0, top, page_x1, bottom))
            )
        )
    ]
    combined = "\n\n".join(band_texts)
    return combined or None


def _markdown_table_from_cells(rows: list[list[str | None]]) -> str:
    """
    Converts a pdfplumber `Table.extract()` result (a list of rows, each a
    list of cell strings, with `None` for empty/merged cells) into a
    Markdown table, applying the Hebrew RTL/PUA fixes to every cell (a
    line-detected table's cells go through raw word extraction just like
    any other text, so they need the same corrections).

    All cells are fixed together in a single `_fix_hebrew_extraction` call
    (one cell per line) rather than one call per cell -- a short cell with
    no RTL-order evidence of its own (e.g. a two-word date-range header
    like "אפריל-מאי") needs to borrow the majority verdict from other
    cells in the same table, exactly like `_fix_hebrew_extraction` already
    does for short lines within a block of plain text. Fixing cells
    independently defeats that cross-line voting entirely -- confirmed on
    a real table where a long, decisive intro cell fixed correctly but a
    short month-range header cell right next to it did not, until this.
    """
    if not rows:
        return ""

    num_cols = max(len(row) for row in rows)
    raw = [
        [
            _strip_pua((row[i] or "").strip()).replace("\n", " ") if i < len(row) else ""
            for i in range(num_cols)
        ]
        for row in rows
    ]
    flat = [cell for row in raw for cell in row]
    # Per-line tidy only: one cell per line, so lines must not be merged.
    fixed_flat = _fix_hebrew_extraction("\n".join(flat), tidy=True).split("\n")
    fixed_it = iter(fixed_flat)
    cleaned = [[next(fixed_it) for _ in range(num_cols)] for _ in raw]
    cleaned = _reverse_rtl_table_columns(cleaned)
    col_widths = [max(3, max(len(row[i]) for row in cleaned)) for i in range(num_cols)]

    def fmt(row: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"

    lines = [fmt(cleaned[0]), "|" + "|".join("-" * (w + 2) for w in col_widths) + "|"]
    lines += [fmt(row) for row in cleaned[1:]]
    return "\n".join(lines)


def _extract_page_with_line_tables(page: Any) -> str | None:
    """
    Extracts a page's content preferring pdfplumber's own line/rect-based
    table detector (`page.find_tables()`) over the word-position heuristic
    in `_extract_form_content_from_words`. When a table has actual visible
    border lines (common in official/government documents), the geometry
    is unambiguous, and pdfplumber's detector uses it directly -- far more
    reliable than inferring table structure from word-alignment statistics
    alone. Found via a real Hebrew Ministry of Education document: a
    bordered 4-column schedule table came out as ~100 fragmented,
    unordered lines through the word-position heuristic (it never
    recognized the page as tabular at all), while `page.find_tables()`
    correctly grouped every cell on the first try.

    Text outside any detected table's bounding box is extracted through
    the existing multi-column-aware plain-text path
    (`_extract_region_text`) and combined with the table(s) top-to-bottom
    by vertical position. Returns None (letting the caller fall back to
    `_extract_form_content_from_words`, then plain text) when no table
    with real row/column structure is found -- most pages have no lines
    at all, and this must not affect them.
    """
    try:
        tables = page.find_tables()
    except Exception:
        return None

    # Only trust tables with genuine row/column structure -- a single
    # row or column is more likely a stray rect than an actual table.
    # Also skip tables whose cells are themselves dense multi-paragraph
    # text (many embedded newlines) rather than short tabular entries --
    # confirmed as a real, serious regression on a Hebrew curriculum PDF
    # with grading-rubric matrices (cells with up to 15 embedded lines of
    # prose each): the whole-cell-as-one-line RTL fix this function
    # relies on (`_markdown_table_from_cells`) is a poor match for that
    # much text packed into "one line," and produced far worse results
    # (violations roughly 10x) than the existing multi-column-aware
    # plain-text path this same document was already handled well by.
    # This heuristic was validated against exactly one short-cell
    # bordered table (a school-year schedule) and should stay scoped to
    # that kind of table, not prose-heavy matrices that merely happen to
    # have drawn borders.
    found: list[tuple[Any, list[list[str | None]]]] = []
    newline_counts: list[int] = []
    for table in tables:
        try:
            data = table.extract()
        except Exception:
            continue
        if len(data) >= 2 and len(data[0]) >= 2:
            found.append((table, data))
            newline_counts += [
                cell.count("\n") for row in data for cell in row if cell
            ]
    if not found:
        return None
    if newline_counts:
        avg_newlines = sum(newline_counts) / len(newline_counts)
        if avg_newlines > 1.0 or max(newline_counts) > 5:
            return None

    # Uses `page.bbox` rather than literal 0/page.height for the same
    # reason as `_detect_row_bands` -- a page whose CropBox origin isn't
    # at y=0 would otherwise crop out of bounds and raise, silently
    # downgrading the whole document via the caller's broad fallback.
    page_x0, page_top, page_x1, page_bottom = page.bbox
    found.sort(key=lambda item: item[0].bbox[1])
    segments: list[str] = []
    cursor = page_top
    for table, data in found:
        top = table.bbox[1]
        if top > cursor + 2:
            region = page.crop((page_x0, cursor, page_x1, top))
            text = _fix_hebrew_page_text(_strip_pua(_extract_banded_region_text(region)))
            if text:
                segments.append(text)
        md_table = _markdown_table_from_cells(data)
        if md_table:
            segments.append(md_table)
        cursor = max(cursor, table.bbox[3])

    if cursor < page_bottom - 2:
        region = page.crop((page_x0, cursor, page_x1, page_bottom))
        text = _fix_hebrew_page_text(_strip_pua(_extract_banded_region_text(region)))
        if text:
            segments.append(text)

    return "\n\n".join(segments) or None


def _extract_form_content_from_words(page: Any) -> str | None:
    """
    Extract form-style content from a PDF page by analyzing word positions.
    This handles borderless forms/tables where words are aligned in columns.

    Returns markdown with proper table formatting:
    - Tables have pipe-separated columns with header separator rows
    - Non-table content is rendered as plain text

    Returns None if the page doesn't appear to be a form-style document,
    indicating that pdfminer should be used instead for better text spacing.
    """
    words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=3)
    if not words:
        return None

    # Group words by their Y position (rows)
    y_tolerance = 5
    rows_by_y: dict[float, list[dict]] = {}
    for word in words:
        y_key = round(word["top"] / y_tolerance) * y_tolerance
        if y_key not in rows_by_y:
            rows_by_y[y_key] = []
        rows_by_y[y_key].append(word)

    # Sort rows by Y position
    sorted_y_keys = sorted(rows_by_y.keys())
    page_width = page.width if hasattr(page, "width") else 612

    # First pass: analyze each row
    row_info: list[dict] = []
    for y_key in sorted_y_keys:
        row_words = sorted(rows_by_y[y_key], key=lambda w: w["x0"])
        if not row_words:
            continue

        first_x0 = row_words[0]["x0"]
        last_x1 = row_words[-1]["x1"]
        line_width = last_x1 - first_x0
        raw_row_text = _strip_pua(" ".join(w["text"] for w in row_words))
        # `combined_text` drives layout decisions (e.g. `is_paragraph` below
        # compares its length), so it must not be tidied; `display_text` is
        # what actually gets written out.
        combined_text = _fix_hebrew_extraction(raw_row_text)
        display_text = _fix_hebrew_extraction(raw_row_text, tidy=True)

        # Count distinct x-position groups (columns)
        x_positions = [w["x0"] for w in row_words]
        x_groups: list[float] = []
        for x in sorted(x_positions):
            if not x_groups or x - x_groups[-1] > 50:
                x_groups.append(x)

        # Determine row type
        is_paragraph = line_width > page_width * 0.55 and len(combined_text) > 60

        # Check for MasterFormat-style partial numbering (e.g., ".1", ".2")
        # These should be treated as list items, not table rows
        has_partial_numbering = False
        if row_words:
            first_word = row_words[0]["text"].strip()
            if PARTIAL_NUMBERING_PATTERN.match(first_word):
                has_partial_numbering = True

        row_info.append(
            {
                "y_key": y_key,
                "words": row_words,
                "text": combined_text,
                "display_text": display_text,
                "x_groups": x_groups,
                "is_paragraph": is_paragraph,
                "num_columns": len(x_groups),
                "has_partial_numbering": has_partial_numbering,
            }
        )

    # Collect ALL x-positions from rows with 3+ columns (table-like rows)
    # This gives us the global column structure
    all_table_x_positions: list[float] = []
    for info in row_info:
        if info["num_columns"] >= 3 and not info["is_paragraph"]:
            all_table_x_positions.extend(info["x_groups"])

    if not all_table_x_positions:
        return None

    # Compute adaptive column clustering tolerance based on gap analysis
    all_table_x_positions.sort()

    # Calculate gaps between consecutive x-positions
    gaps = []
    for i in range(len(all_table_x_positions) - 1):
        gap = all_table_x_positions[i + 1] - all_table_x_positions[i]
        if gap > 5:  # Only significant gaps
            gaps.append(gap)

    # Determine optimal tolerance using statistical analysis
    if gaps and len(gaps) >= 3:
        # Use 70th percentile of gaps as threshold (balances precision/recall)
        sorted_gaps = sorted(gaps)
        percentile_70_idx = int(len(sorted_gaps) * 0.70)
        adaptive_tolerance = sorted_gaps[percentile_70_idx]

        # Clamp tolerance to reasonable range [25, 50]
        adaptive_tolerance = max(25, min(50, adaptive_tolerance))
    else:
        # Fallback to conservative value
        adaptive_tolerance = 35

    # Compute global column boundaries using adaptive tolerance
    global_columns: list[float] = []
    for x in all_table_x_positions:
        if not global_columns or x - global_columns[-1] > adaptive_tolerance:
            global_columns.append(x)

    # Adaptive max column check based on page characteristics
    # Calculate average column width
    if len(global_columns) > 1:
        content_width = global_columns[-1] - global_columns[0]
        avg_col_width = content_width / len(global_columns)

        # Forms with very narrow columns (< 30px) are likely dense text
        if avg_col_width < 30:
            return None

        # Compute adaptive max based on columns per inch
        # Typical forms have 3-8 columns per inch
        columns_per_inch = len(global_columns) / (content_width / 72)

        # If density is too high (> 10 cols/inch), likely not a form
        if columns_per_inch > 10:
            return None

        # Adaptive max: allow more columns for wider pages
        # Standard letter is 612pt wide, so scale accordingly
        adaptive_max_columns = int(20 * (page_width / 612))
        adaptive_max_columns = max(15, adaptive_max_columns)  # At least 15

        if len(global_columns) > adaptive_max_columns:
            return None
    else:
        # Single column, not a form
        return None

    # Now classify each row as table row or not
    # A row is a table row if it has words that align with 2+ of the global columns
    for info in row_info:
        if info["is_paragraph"]:
            info["is_table_row"] = False
            continue

        # Rows with partial numbering (e.g., ".1", ".2") are list items, not table rows
        if info["has_partial_numbering"]:
            info["is_table_row"] = False
            continue

        # Count how many global columns this row's words align with
        aligned_columns: set[int] = set()
        for word in info["words"]:
            word_x = word["x0"]
            for col_idx, col_x in enumerate(global_columns):
                if abs(word_x - col_x) < 40:
                    aligned_columns.add(col_idx)
                    break

        # If row uses 2+ of the established columns, it's a table row
        info["is_table_row"] = len(aligned_columns) >= 2

    # Find table regions (consecutive table rows)
    table_regions: list[tuple[int, int]] = []  # (start_idx, end_idx)
    i = 0
    while i < len(row_info):
        if row_info[i]["is_table_row"]:
            start_idx = i
            while i < len(row_info) and row_info[i]["is_table_row"]:
                i += 1
            end_idx = i
            table_regions.append((start_idx, end_idx))
        else:
            i += 1

    # Check if enough rows are table rows (at least 20%)
    total_table_rows = sum(end - start for start, end in table_regions)
    if len(row_info) > 0 and total_table_rows / len(row_info) < 0.2:
        return None

    # Build output - collect table data first, then format with proper column widths
    result_lines: list[str] = []
    num_cols = len(global_columns)

    # Helper function to extract cells from a row
    def extract_cells(info: dict) -> list[str]:
        cells: list[str] = ["" for _ in range(num_cols)]
        for word in info["words"]:
            word_x = word["x0"]
            # Find the correct column using boundary ranges
            assigned_col = num_cols - 1  # Default to last column
            for col_idx in range(num_cols - 1):
                col_end = global_columns[col_idx + 1]
                if word_x < col_end - 20:
                    assigned_col = col_idx
                    break
            word_text = _strip_pua(word["text"])
            if cells[assigned_col]:
                cells[assigned_col] += " " + word_text
            else:
                cells[assigned_col] = word_text
        # Deliberately fixed one cell at a time (not combined row-wise
        # like _markdown_table_from_cells): tried combining them for the
        # same cross-cell majority-voting reasoning, but on this word-
        # position heuristic's output -- rows often mix real content
        # cells with sparse numeric/symbol fragments -- a row-level
        # majority computed from just one or two decisive cells proved
        # unreliable and mis-flipped otherwise-fine sibling cells,
        # regressing two previously-clean real documents (a curriculum
        # PDF and a math worksheet) meaningfully. An isolated indecisive
        # cell here is left as extracted rather than risk that.
        return [_fix_hebrew_extraction(cell) for cell in cells]

    # Process rows, collecting table data for proper formatting
    idx = 0
    while idx < len(row_info):
        info = row_info[idx]

        # Check if this row starts a table region
        table_region = None
        for start, end in table_regions:
            if idx == start:
                table_region = (start, end)
                break

        if table_region:
            start, end = table_region
            # Collect all rows in this table
            table_data: list[list[str]] = []
            for table_idx in range(start, end):
                cells = extract_cells(row_info[table_idx])
                table_data.append(cells)

            table_data = _reverse_rtl_table_columns(table_data)

            # Calculate column widths for this table
            if table_data:
                total_cells = sum(len(row) for row in table_data)
                filled_cells = sum(
                    1 for row in table_data for cell in row if cell.strip()
                )
                fill_ratio = filled_cells / total_cells if total_cells else 0

                if fill_ratio < 0.2:
                    # Grids/answer boxes meant for hand-filling (common in
                    # worksheet-style PDFs -- multiplication grids, blank
                    # tic-tac-toe-style boards, fill-in-the-blank tables)
                    # get misdetected as data tables by the column-alignment
                    # heuristic above but are almost entirely empty cells.
                    # Rendering that as a full pipe-table is pure structural
                    # noise -- dozens of blank "|     |     |     |" rows
                    # that bury whatever real content sits nearby, and on a
                    # worksheet-heavy document can dominate the whole
                    # output. Below this fill ratio, skip the table framing
                    # and just emit any actual cell content (e.g. a stray
                    # "x" mark) as plain text, dropping empty rows entirely
                    # -- a real, denser grid full of X's/answers stays well
                    # above this threshold and still renders as a table.
                    for row in table_data:
                        non_empty = [cell for cell in row if cell.strip()]
                        if non_empty:
                            result_lines.append(" ".join(non_empty))
                else:
                    col_widths = [
                        max(len(row[col]) for row in table_data)
                        for col in range(num_cols)
                    ]
                    # Ensure minimum width of 3 for separator dashes
                    col_widths = [max(w, 3) for w in col_widths]

                    # Format header row
                    header = table_data[0]
                    header_str = (
                        "| "
                        + " | ".join(
                            cell.ljust(col_widths[i]) for i, cell in enumerate(header)
                        )
                        + " |"
                    )
                    result_lines.append(header_str)

                    # Format separator row
                    separator = (
                        "| "
                        + " | ".join("-" * col_widths[i] for i in range(num_cols))
                        + " |"
                    )
                    result_lines.append(separator)

                    # Format data rows
                    for row in table_data[1:]:
                        row_str = (
                            "| "
                            + " | ".join(
                                cell.ljust(col_widths[i]) for i, cell in enumerate(row)
                            )
                            + " |"
                        )
                        result_lines.append(row_str)

            idx = end  # Skip to end of table region
        else:
            # Check if we're inside a table region (not at start)
            in_table = False
            for start, end in table_regions:
                if start < idx < end:
                    in_table = True
                    break

            if not in_table:
                # Non-table content
                result_lines.append(info["display_text"])
            idx += 1

    return "\n".join(result_lines)


def _extract_tables_from_words(page: Any) -> list[list[list[str]]]:
    """
    Extract tables from a PDF page by analyzing word positions.
    This handles borderless tables where words are aligned in columns.

    This function is designed for structured tabular data (like invoices),
    not for multi-column text layouts in scientific documents.
    """
    words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=3)
    if not words:
        return []

    # Group words by their Y position (rows)
    y_tolerance = 5
    rows_by_y: dict[float, list[dict]] = {}
    for word in words:
        y_key = round(word["top"] / y_tolerance) * y_tolerance
        if y_key not in rows_by_y:
            rows_by_y[y_key] = []
        rows_by_y[y_key].append(word)

    # Sort rows by Y position
    sorted_y_keys = sorted(rows_by_y.keys())

    # Find potential column boundaries by analyzing x positions across all rows
    all_x_positions = []
    for words_in_row in rows_by_y.values():
        for word in words_in_row:
            all_x_positions.append(word["x0"])

    if not all_x_positions:
        return []

    # Cluster x positions to find column starts
    all_x_positions.sort()
    x_tolerance_col = 20
    column_starts: list[float] = []
    for x in all_x_positions:
        if not column_starts or x - column_starts[-1] > x_tolerance_col:
            column_starts.append(x)

    # Need at least 3 columns but not too many (likely text layout, not table)
    if len(column_starts) < 3 or len(column_starts) > 10:
        return []

    # Find rows that span multiple columns (potential table rows)
    table_rows = []
    for y_key in sorted_y_keys:
        words_in_row = sorted(rows_by_y[y_key], key=lambda w: w["x0"])

        # Assign words to columns
        row_data = [""] * len(column_starts)
        for word in words_in_row:
            # Find the closest column
            best_col = 0
            min_dist = float("inf")
            for i, col_x in enumerate(column_starts):
                dist = abs(word["x0"] - col_x)
                if dist < min_dist:
                    min_dist = dist
                    best_col = i

            word_text = _strip_pua(word["text"])
            if row_data[best_col]:
                row_data[best_col] += " " + word_text
            else:
                row_data[best_col] = word_text

        row_data = [_fix_hebrew_extraction(cell) for cell in row_data]

        # Only include rows that have content in multiple columns
        non_empty = sum(1 for cell in row_data if cell.strip())
        if non_empty >= 2:
            table_rows.append(row_data)

    # Validate table quality - tables should have:
    # 1. Enough rows (at least 3 including header)
    # 2. Short cell content (tables have concise data, not paragraphs)
    # 3. Consistent structure across rows
    if len(table_rows) < 3:
        return []

    # Check if cells contain short, structured data (not long text)
    long_cell_count = 0
    total_cell_count = 0
    for row in table_rows:
        for cell in row:
            if cell.strip():
                total_cell_count += 1
                # If cell has more than 30 chars, it's likely prose text
                if len(cell.strip()) > 30:
                    long_cell_count += 1

    # If more than 30% of cells are long, this is probably not a table
    if total_cell_count > 0 and long_cell_count / total_cell_count > 0.3:
        return []

    return [table_rows]


def _non_blank_line_count(text: str) -> int:
    return sum(1 for line in text.split("\n") if line.strip())


class PdfConverter(DocumentConverter):
    """
    Converts PDFs to Markdown.
    Supports extracting tables into aligned Markdown format (via pdfplumber).
    Falls back to pdfminer if pdfplumber is missing or fails.
    """

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()

        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True

        for prefix in ACCEPTED_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True

        return False

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        if _dependency_exc_info is not None:
            raise MissingDependencyException(
                MISSING_DEPENDENCY_MESSAGE.format(
                    converter=type(self).__name__,
                    extension=".pdf",
                    feature="pdf",
                )
            ) from _dependency_exc_info[1].with_traceback(
                _dependency_exc_info[2]
            )  # type: ignore[union-attr]

        assert isinstance(file_stream, io.IOBase)

        # Read file stream into BytesIO for compatibility with pdfplumber
        pdf_bytes = io.BytesIO(file_stream.read())

        try:
            # Single pass: check every page for form-style content.
            # Pages with tables/forms get rich extraction; plain-text
            # pages are collected separately. page.close() is called
            # after each page to free pdfplumber's cached objects and
            # keep memory usage constant regardless of page count.
            markdown_chunks: list[str] = []
            _document_bracket_votes[:] = [0, 0]
            form_page_count = 0
            # Pages laid out as columns of running text; see the engine
            # choice after the loop.
            column_page_count = 0
            plain_page_indices: list[int] = []
            # Tracked independently of markdown_chunks because
            # markdown_chunks is discarded entirely below when no page
            # has form-style content (the whole-document pdfminer
            # fallback is used instead) -- these notes must survive that.
            image_page_counts: dict[int, int] = {}
            # 1-based page numbers whose layout-aware extraction path
            # raised and fell back to a plain per-page re-extraction --
            # surfaced as a validation warning by convert_driver.py so a
            # silently-degraded page doesn't look identical to a clean
            # conversion.
            degraded_page_numbers: list[int] = []

            with pdfplumber.open(pdf_bytes) as pdf:
                for page_idx, raw_page in enumerate(pdf.pages):
                    page = raw_page
                    try:
                        _mark_list_tabs(raw_page)
                        page = _drop_overlapped_spaces(raw_page)
                        page_content = _extract_page_with_line_tables(page)
                        if page_content is None:
                            if _has_flowing_text_columns(page):
                                column_page_count += 1
                            else:
                                page_content = _extract_form_content_from_words(page)
                        image_count = len(raw_page.images)
                        if image_count > 0:
                            image_page_counts[page_idx + 1] = image_count

                        if page_content is not None:
                            form_page_count += 1
                            if page_content.strip():
                                markdown_chunks.append(page_content)
                        else:
                            plain_page_indices.append(page_idx)
                            text = _extract_plain_page_text(page)
                            if text and text.strip():
                                fixed_text = _fix_hebrew_page_text(_strip_pua(text.strip()))
                                markdown_chunks.append(fixed_text)
                    except Exception:
                        # A single page's layout-aware extraction (bands,
                        # gutters, line-tables) can hit an edge case the
                        # heuristics above don't yet handle -- confirmed
                        # in practice via a page whose CropBox origin
                        # wasn't at y=0, which raised inside
                        # `_detect_row_bands`. Before this per-page
                        # isolation, that single page's exception
                        # propagated out of this whole loop and silently
                        # downgraded the ENTIRE document (all pages, not
                        # just the one) to the much cruder
                        # whole-document pdfminer fallback below --
                        # confirmed as the actual root cause of an
                        # otherwise-fine 145-page document converting
                        # badly throughout. Falling back to just this
                        # page's own plain `extract_text()` contains the
                        # damage to the one page that triggered it.
                        degraded_page_numbers.append(page_idx + 1)
                        try:
                            text = page.extract_text()
                            if text and text.strip():
                                fixed_text = _fix_hebrew_page_text(
                                    _strip_pua(text.strip())
                                )
                                markdown_chunks.append(fixed_text)
                        except Exception:
                            pass  # this page's text is lost, but the rest of the document isn't

                    raw_page.close()  # Free cached page data immediately

            # If no pages had form-style content, use pdfminer for
            # the whole document (better text spacing for prose) -- unless
            # the per-page text is the more trustworthy of the two:
            #
            # - a page was read as columns. pdfminer knows nothing of the
            #   column order worked out above, so switching to it would
            #   throw that away for the whole document;
            # - pdfminer shatters the text. A PDF that positions every word
            #   separately comes out of it one or two words to a line (a
            #   3-page document became 1,062 lines instead of 130). Nothing
            #   about such a file says so in advance, so the two results are
            #   compared: far more lines for the same text means fragments.
            page_markdown = "\n\n".join(markdown_chunks).strip()
            if form_page_count == 0 and column_page_count == 0:
                pdf_bytes.seek(0)
                markdown = _fix_hebrew_page_text(
                    _strip_pua(pdfminer.high_level.extract_text(pdf_bytes))
                )
                if _non_blank_line_count(markdown) > 1.5 * _non_blank_line_count(
                    page_markdown
                ):
                    markdown = page_markdown
            else:
                markdown = page_markdown

        except Exception:
            # Fallback if pdfplumber fails
            pdf_bytes.seek(0)
            markdown = _fix_hebrew_page_text(
                _strip_pua(pdfminer.high_level.extract_text(pdf_bytes))
            )

        # Fallback if still empty
        if not markdown:
            pdf_bytes.seek(0)
            markdown = _fix_hebrew_page_text(
                _strip_pua(pdfminer.high_level.extract_text(pdf_bytes))
            )

        # Post-process to merge MasterFormat-style partial numbering with following text
        markdown = _merge_partial_numbering_lines(markdown)

        # Images (diagrams, illustrations, or numerals/content drawn as
        # graphics rather than text) are invisible to every text-extraction
        # path above -- there is no text layer to recover them from
        # without OCR. Silently skipping them makes the Markdown output
        # look like a faithful copy of the document when it isn't; a
        # summary of how many were skipped, and on which pages, at least
        # makes that gap visible instead of invisible.
        if image_page_counts:
            total_images = sum(image_page_counts.values())
            images_plural = "s" if total_images != 1 else ""
            pages_plural = "s" if len(image_page_counts) != 1 else ""
            page_list = ", ".join(
                f"p.{page}: {count}" for page, count in sorted(image_page_counts.items())
            )
            markdown += (
                f"\n\n---\n\n*This document contains {total_images} image{images_plural} "
                f"across {len(image_page_counts)} page{pages_plural} not represented "
                "above -- visual content such as diagrams, illustrations, or numerals "
                "drawn as graphics isn't recoverable as text without OCR. Per-page "
                f"counts: {page_list}.*"
            )

        # Surface per-page fallback recovery the same way as the image
        # note above -- a page that hit the try/except in the loop got a
        # cruder extraction than the rest of the document (no band/column/
        # table awareness), so it deserves a visible flag rather than
        # looking identical to a normal conversion.
        if degraded_page_numbers:
            pages_plural = "s" if len(degraded_page_numbers) != 1 else ""
            page_list = ", ".join(f"p.{p}" for p in sorted(degraded_page_numbers))
            markdown += (
                f"\n\n---\n\n*{len(degraded_page_numbers)} page{pages_plural} "
                f"({page_list}) hit an internal error during layout-aware extraction "
                "and fell back to plain text extraction for that page only -- text on "
                "those pages may be less reliably ordered than the rest of this "
                "document.*"
            )

        return DocumentConverterResult(markdown=markdown)

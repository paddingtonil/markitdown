#!/usr/bin/env python3
"""
Small CLI driver used by MarkItDownMac.

Usage:
    python convert_driver.py <path-to-file>

Converts the given file to Markdown and runs a lightweight post-conversion
validation pass over the result, then prints a single JSON object to stdout:
{"markdown": "...", "warnings": [...], "needs_confirmation": bool}.
`warnings` is empty when validation found nothing to flag. `needs_confirmation`
is true only when the content looks unreliable enough (currently: a Hebrew
orthography-violation rate far above this project's known-good baselines, or
several lines with a symbol where a Hebrew letter should be, or several Hebrew
lines with their brackets back to front, or OCR output
from a PDF without a text layer that does not read as text) that the caller
should ask the user before treating the conversion as done,
rather than just displaying a warning -- see `_validate`. On failure, prints
a clear message to stderr and exits with a non-zero status (no JSON is
printed in that case).

Engine routing (PDF only):
    Most files go through the vendored `markitdown` package unchanged. For
    `.pdf` files, this driver additionally routes non-Hebrew documents
    through `docling` (IBM's ML-based layout engine) first, since it
    handles complex multi-column/table layouts far better than the
    whitespace-gap heuristic in `engine/vendor/markitdown`'s `_pdf_converter.py`.
    Hebrew-heavy PDFs are deliberately kept on the `markitdown` path --
    `_pdf_converter.py` has proven, hand-tuned RTL handling that `docling`
    does not match (it has a known bug reversing Hebrew word order as of
    early 2026: https://github.com/docling-project/docling/issues/3021).
    A `docling` failure of any kind (unsupported PDF, missing models, a
    native crash, etc.) silently falls back to `markitdown` rather than
    breaking a conversion that used to work. `docling` runs in a child
    process for that reason: a crash inside its native libraries kills
    the process it happens in, so only a separate process leaves this one
    alive to fall back -- see `_convert_via_docling_subprocess`.

This script is copied into the app bundle's Contents/Resources and is run
by the bundled Python interpreter as a subprocess from the Swift app
(see ConversionService.swift).
"""

import json
import os
import re
import sys

# Matches the summary note `_pdf_converter.py` appends when a PDF contains
# images (diagrams, illustrations, numerals drawn as graphics) that have no
# text-layer representation -- see that file for why. Reusing its own
# wording here (rather than recomputing anything) keeps this in sync with
# whatever the converter actually decided to report. Only ever appears in
# markitdown's own output (docling has no equivalent note), so this simply
# won't match on docling-produced Markdown -- which is correct, not a gap.
_IMAGE_NOTE_PATTERN = re.compile(
    r"This document contains (\d[\d,]*) images? across (\d[\d,]*) pages? "
    r"not represented above"
)

# Matches the trailing note `_pdf_converter.py` appends when one or more
# pages hit an internal error during layout-aware extraction and fell back
# to plain per-page text extraction (see the try/except around the main
# per-page loop there). Before that per-page isolation existed, a single
# page's exception silently downgraded the *entire* document to a much
# cruder whole-document fallback -- confirmed as the real root cause of an
# otherwise-fine 145-page document converting badly throughout, caught only
# because the page that failed left this note behind. Surfacing it as a
# warning means a future not-yet-seen per-page edge case is visible instead
# of silently degrading quality with no signal at all.
_DEGRADED_PAGE_NOTE_PATTERN = re.compile(
    r"(\d[\d,]*) pages? \(([^)]+)\) hit an internal error during "
    r"layout-aware extraction"
)

# Hebrew orthography violations (see `_hebrew_orthography_violations` in
# _pdf_converter.py) scale with document length, so a raw count isn't a
# usable quality signal on its own -- a 150-page document with 40
# violations (mostly geresh-abbreviation false positives, per that
# project's regression baselines) is fine, but 40 violations in a 2-page
# document is a real problem. This threshold is a *rate*: violations per
# 1,000 Hebrew letters. Measured (2026-10-08) on the six Hebrew regression
# documents: 0.31 to 1.78 per 1,000, the top end being a worksheet with only
# 562 Hebrew letters in its output, where one decorative-spacing artifact
# alone is 1.78. The one known-bad document (a corrupted font encoding) came
# out at 8.9. That is a single data point, so 5.0 is a judgement call between
# the two, not a fitted value. Small samples are noisy: with ~500-1,000
# letters, three false positives would cross it (the user can accept such a
# file with "Use Anyway").
_HEBREW_VIOLATION_RATE_THRESHOLD = 5.0


def _hebrew_violation_rate(markdown: str) -> float | None:
    """
    Returns violations per 1,000 Hebrew letters, or None if the document
    has too little Hebrew content (fewer than 500 Hebrew letters) for the
    rate to be a meaningful signal either way -- avoids a short document
    with one bad word producing an alarming-looking rate.
    """
    try:
        from markitdown.converters._pdf_converter import (
            _hebrew_orthography_violations,
        )
    except Exception:
        return None

    hebrew_letters = sum(
        1 for ch in markdown if _HEBREW_BLOCK_START <= ord(ch) <= _HEBREW_BLOCK_END
    )
    if hebrew_letters < 500:
        return None
    violations = _hebrew_orthography_violations(markdown) - len(
        _HEBREW_ABBREVIATION_STEM_PATTERN.findall(markdown)
    )
    return violations / hebrew_letters * 1000


# The stem of a Hebrew abbreviation: `תנ` in `תנ"ך`, `עמ` in `עמ'`. The
# orthography counter splits words at the gershayim/geresh, sees a word that
# ends in the regular form of a letter that has a final form, and counts a
# violation -- on correct text. Text dense in such abbreviations (Bible and
# rabbinic sources: תנ"ך, רמב"ם, חז"ל, עמ') could cross the threshold on
# those alone: a correct 1,140-character test text read 7.6 per 1,000.
#
# Each match is exactly one violation the counter reports (a word of two
# letters or more ending in כ/מ/נ/פ/צ), so the matches are subtracted from
# its count. A gershayim only counts when a Hebrew letter follows it, so an
# ordinary closing quotation mark is not mistaken for one. This is applied
# to the validation rate only: the same counter also decides the direction
# of each line in `_pdf_converter.py`, and changing it there would change
# the text of documents that already convert correctly.
_HEBREW_ABBREVIATION_STEM_PATTERN = re.compile(
    r"(?<=[א-ת])[כמנפצ](?:[\"״](?=[א-ת])|['׳])"
)


# An ASCII symbol standing where a Hebrew letter should be, inside a Hebrew
# word (`ני#וי` for `ניסוי`) or glued to its end (`ארגו&` for `ארגון`). The
# violation rate above cannot see this -- it only looks at Hebrew letters, so
# a symbol costs it nothing: a real document with 41 such lines still read
# 0.74 per 1,000, and even added to the rate they would only reach ~1.3,
# well under its threshold. So this is its own signal, counted in lines.
#
# Measured (2026-10-09) on the correct output of the seven Hebrew regression
# documents this matches 0 lines; on the one known-corrupt output it matches
# dozens (13 and 35 lines for its two halves, ~40 together). A symbol at the
# *start* of a word (`#ישראל`, a hashtag) is deliberately not matched, and
# neither is a digit glued after a word: footnote markers like `בעולם8` and
# labels like `פעילות1:` are legitimate and did match in two clean documents.
# The threshold of 3 sits in the gap between 0 and dozens, so a stray
# `R&D`-style token won't trip it; it is a judgement call, not a fitted value.
_HEBREW_SYMBOL_SUBSTITUTION_PATTERN = re.compile(
    r"[א-ת][#&][א-ת]|[א-ת]{2,}[#&](?![0-9A-Za-z])"
)
_HEBREW_SYMBOL_SUBSTITUTION_LINE_THRESHOLD = 3


def _hebrew_symbol_substitution_lines(markdown: str) -> list[str]:
    return [
        line
        for line in markdown.splitlines()
        if _HEBREW_SYMBOL_SUBSTITUTION_PATTERN.search(line)
    ]


# A Hebrew line whose brackets are back to front: the first one closes and
# the last one opens, `)AI( כלי הבינה`. Brackets are the one place where a
# right-to-left line put back in the wrong order shows itself without a
# dictionary -- the same lines have their Latin words and numbers on the wrong
# side of the Hebrew, which reads as plausible text and trips nothing else.
#
# Measured (2026-10-10) on six Hebrew documents: before the reordering fix in
# `_pdf_converter.py` (`_visual_to_logical`) four of them had 3, 13, 21 and 45
# such lines; after it, 0 or 1 each (the 1 is a table cell left unreordered).
# The threshold sits between those groups.
_REVERSED_BRACKET_LINE_THRESHOLD = 5
_BRACKET_PATTERN = re.compile(r"[()\[\]]")
_HEBREW_LETTER_PATTERN = re.compile(r"[א-ת]")


def _reversed_bracket_lines(markdown: str) -> list[str]:
    reversed_lines = []
    for line in markdown.splitlines():
        if not _HEBREW_LETTER_PATTERN.search(line):
            continue
        brackets = _BRACKET_PATTERN.findall(line)
        if len(brackets) >= 2 and brackets[0] in ")]" and brackets[-1] in "([":
            reversed_lines.append(line)
    return reversed_lines


# Unicode block for Hebrew (letters, points, punctuation).
_HEBREW_BLOCK_START = 0x0590
_HEBREW_BLOCK_END = 0x05FF

# A PDF whose sampled pages are at least this fraction Hebrew (by letter
# count, across all scripts) is treated as "meaningfully Hebrew" and kept
# on the proven `markitdown` RTL path rather than routed to `docling`.
# Deliberately not tiny -- a document that happens to quote a Hebrew phrase
# once shouldn't lose the layout benefits of `docling` for the rest of it.
_HEBREW_FRACTION_THRESHOLD = 0.15


# docling's bundled OCR engine (RapidOCR) reads Latin and Chinese script only.
# A scanned PDF in Hebrew has no text layer, so the Hebrew-fraction sample
# sees no letters at all, the file is routed to docling, and the OCR returns
# confident-looking noise (`UUUILIO ¿LXI XU LALOA IACI`). None of the Hebrew
# checks above can see that, since the output contains no Hebrew letters.
#
# So for text that came from OCR (see `ocr_derived` in main()), this measures
# how much of it reads as ordinary words: of the tokens containing a letter,
# the share that is a lowercase or Capitalised ASCII word of two letters or
# more. All-caps tokens are left out of the numerator on purpose -- the noise
# is mostly capitals, while real text only uses them for headings.
_OCR_WORDLIKE_PATTERN = re.compile(r"[A-Za-z][a-z]+(?:['’-][a-z]+)*")
_OCR_MIN_LETTER_TOKENS = 20
_OCR_WORDLIKE_THRESHOLD = 0.3


def _ocr_wordlike_fraction(markdown: str) -> float | None:
    """
    Share of letter-bearing tokens that look like ordinary Latin-script
    words, or None when there are too few tokens to judge either way.
    """
    letter_tokens = 0
    wordlike = 0
    for token in markdown.split():
        if not any(ch.isalpha() for ch in token):
            continue
        letter_tokens += 1
        core = token.strip("\"'“”‘’()[]{}<>.,:;!?*_#|-–—/")
        if _OCR_WORDLIKE_PATTERN.fullmatch(core):
            wordlike += 1
    if letter_tokens < _OCR_MIN_LETTER_TOKENS:
        return None
    return wordlike / letter_tokens


def _validate(markdown: str, ocr_derived: bool = False) -> tuple[list[str], bool]:
    """
    Lightweight, low-false-positive checks on the converted Markdown.
    Deliberately conservative: a warning here should mean something a user
    would actually want to know about, not routine heuristic noise. Applies
    the same way regardless of which engine (markitdown or docling)
    produced the text.

    Returns (warnings, needs_confirmation). Most warnings are purely
    informational (e.g. "N images couldn't be converted to text" -- true of
    essentially every scanned diagram, not a sign anything went wrong) and
    don't set needs_confirmation. It's set only when the content itself
    looks unreliable enough that silently handing the file over would be
    misleading -- currently: a Hebrew orthography-violation rate far above
    this project's own known-good baselines, or at least a few lines with a
    symbol standing where a Hebrew letter should be (the rate cannot see
    those; see `_HEBREW_SYMBOL_SUBSTITUTION_PATTERN`), or several Hebrew
    lines with their brackets back to front (mixed-direction lines put
    together in the wrong order; see `_reversed_bracket_lines`), or OCR output that
    does not read as text (`ocr_derived`: the PDF had no text layer and the
    text was read from page images; see `_OCR_WORDLIKE_PATTERN`). There is no automatic
    correction available for that case (the two engines available on this
    machine were both tried and neither recovers a genuinely corrupted
    font encoding -- see README's Hebrew/RTL section), so the honest move
    is to flag it for a human to look at rather than claim success.
    """
    warnings: list[str] = []

    if not markdown.strip():
        warnings.append(
            "No readable content was extracted from this file -- it may be "
            "a scanned document, an image-only file, or contain no "
            "extractable text."
        )
        return warnings, True

    image_match = _IMAGE_NOTE_PATTERN.search(markdown)
    if image_match:
        image_count, page_count = image_match.group(1), image_match.group(2)
        warnings.append(
            f"{image_count} image(s) across {page_count} page(s) are visual "
            "content (diagrams, illustrations, numerals drawn as graphics) "
            "that couldn't be converted to text -- see the note at the end "
            "of the Markdown for details."
        )

    degraded_match = _DEGRADED_PAGE_NOTE_PATTERN.search(markdown)
    if degraded_match:
        page_count, page_list = degraded_match.group(1), degraded_match.group(2)
        warnings.append(
            f"{page_count} page(s) ({page_list}) hit an internal error during "
            "extraction and used a simpler fallback for just that page -- "
            "text on those pages may be less reliably ordered than the rest "
            "of this document."
        )

    needs_confirmation = False
    rate = _hebrew_violation_rate(markdown)
    if rate is not None and rate > _HEBREW_VIOLATION_RATE_THRESHOLD:
        needs_confirmation = True
        warnings.append(
            f"This document's Hebrew text shows {rate:.1f} orthography "
            "violations per 1,000 letters, well above what a correctly "
            "extracted Hebrew document typically shows -- the text may be "
            "unreliable (e.g. a corrupted font encoding this app couldn't "
            "automatically recover from). Please review the output before "
            "relying on it."
        )

    substituted = _hebrew_symbol_substitution_lines(markdown)
    if len(substituted) >= _HEBREW_SYMBOL_SUBSTITUTION_LINE_THRESHOLD:
        needs_confirmation = True
        example = substituted[0].strip()[:80]
        warnings.append(
            f"{len(substituted)} lines contain a symbol such as # or & where a "
            f"Hebrew letter should be (for example: \"{example}\") -- a sign "
            "of a font whose characters this app could not decode correctly. "
            "Please review the output before relying on it."
        )

    reversed_brackets = _reversed_bracket_lines(markdown)
    if len(reversed_brackets) >= _REVERSED_BRACKET_LINE_THRESHOLD:
        needs_confirmation = True
        example = reversed_brackets[0].strip()[:80]
        warnings.append(
            f"{len(reversed_brackets)} Hebrew lines have their brackets back to "
            f"front (for example: \"{example}\") -- a sign that lines mixing "
            "Hebrew with English words or numbers were put together in the "
            "wrong order. Please review the output before relying on it."
        )

    if ocr_derived:
        wordlike = _ocr_wordlike_fraction(markdown)
        if wordlike is not None and wordlike < _OCR_WORDLIKE_THRESHOLD:
            needs_confirmation = True
            warnings.append(
                "This PDF has no text layer (it is a scan), so its text was read "
                "from the page images, and the result does not read as text "
                f"(only {wordlike:.0%} of it looks like words). The text "
                "recognition in this app cannot read Hebrew, so a scanned Hebrew "
                "document comes out as meaningless characters. Please review the "
                "output before relying on it."
            )

    return warnings, needs_confirmation


# Sampling for the Hebrew-fraction check below is adaptive rather than a
# fixed page list: it starts with a small, evenly-spaced sample and keeps
# widening it until either enough alphabetic signal has been seen or the
# page/document budget runs out. A fixed small sample can land entirely on
# structurally sparse pages -- caught on a real 39-page math worksheet
# booklet where an initial 5-page sample (first three + two from the
# middle) scored zero alphabetic characters, because those particular
# pages were almost entirely image-based numerals/icon-font grids with no
# text layer, despite the document containing substantial Hebrew prose
# elsewhere (instructions, section headers). That silently misrouted a
# Hebrew document to `docling` instead of the proven `markitdown` RTL path.
_HEBREW_SAMPLE_START = 5
_HEBREW_SAMPLE_MAX = 60
_HEBREW_MIN_ALPHA_CHARS = 200


def _evenly_spaced_indices(num_pages: int, count: int) -> list[int]:
    """Picks up to `count` page indices spread evenly across the document
    (always including the first and last page when count > 1), rather than
    clustering near the front."""
    if num_pages <= count:
        return list(range(num_pages))
    if count <= 1:
        return [0]
    step = (num_pages - 1) / (count - 1)
    return sorted({round(i * step) for i in range(count)})


def _sample_pdf_letters(path: str) -> tuple[int, int]:
    """
    Counts (Hebrew letters, all alphabetic characters) in a sample of a
    PDF's text layer: extracts text from a sample of pages via pdfplumber and counts
    characters in the Hebrew Unicode block against all alphabetic
    characters (any script). The sample starts small and evenly spaced
    (see `_evenly_spaced_indices`) and widens -- up to `_HEBREW_SAMPLE_MAX`
    pages or the whole document, whichever is smaller -- if it hasn't
    collected at least `_HEBREW_MIN_ALPHA_CHARS` alphabetic characters yet,
    so a document with long sparse/image-heavy stretches doesn't get judged
    on pages that happen to carry almost no text either way.

    Returns (0, 0) if the PDF can't be opened/sampled at all -- that
    failure will surface again, more informatively, when the actual
    conversion is attempted.
    """
    try:
        import pdfplumber
    except Exception:
        return 0, 0

    hebrew_count = 0
    alpha_count = 0
    try:
        with pdfplumber.open(path) as pdf:
            num_pages = len(pdf.pages)
            checked: set[int] = set()

            def scan(indices: list[int]) -> None:
                nonlocal hebrew_count, alpha_count
                for i in indices:
                    if i in checked:
                        continue
                    checked.add(i)
                    text = pdf.pages[i].extract_text() or ""
                    for ch in text:
                        if ch.isalpha():
                            alpha_count += 1
                            if _HEBREW_BLOCK_START <= ord(ch) <= _HEBREW_BLOCK_END:
                                hebrew_count += 1

            sample_size = min(_HEBREW_SAMPLE_START, num_pages)
            scan(_evenly_spaced_indices(num_pages, sample_size))

            while (
                alpha_count < _HEBREW_MIN_ALPHA_CHARS
                and sample_size < num_pages
                and sample_size < _HEBREW_SAMPLE_MAX
            ):
                sample_size = min(sample_size * 3, _HEBREW_SAMPLE_MAX, num_pages)
                scan(_evenly_spaced_indices(num_pages, sample_size))
    except Exception:
        return 0, 0

    return hebrew_count, alpha_count


def _estimate_hebrew_fraction(path: str) -> float:
    """
    Fraction of a PDF's sampled alphabetic characters that are Hebrew (see
    `_sample_pdf_letters`); 0.0 (i.e. "not Hebrew") when the sample has no
    letters at all.
    """
    hebrew_count, alpha_count = _sample_pdf_letters(path)
    if alpha_count == 0:
        return 0.0
    return hebrew_count / alpha_count


def _locate_model_cache_dir() -> str | None:
    """
    Resolves the bundled Hugging Face model cache directory (downloaded at
    build time so docling never needs network access -- see
    tools/build_python_runtime.sh), mirroring the same bundle-vs-dev
    fallback pattern `ConversionService.swift`'s `locateRuntime()` uses for
    the Python runtime itself:

    1. Packaged app: this script lives at Contents/Resources/convert_driver.py,
       so the cache is the sibling directory Contents/Resources/hf-cache.
    2. Dev layout: this script lives at engine/convert_driver.py under the
       project root, so the cache is build/hf-cache.

    Returns None if neither exists, in which case docling falls back to its
    own default cache location and may attempt a network download -- this
    should only happen if the build step that populates hf-cache was
    skipped.
    """
    here = os.path.dirname(os.path.abspath(__file__))

    packaged_candidate = os.path.join(here, "hf-cache")
    if os.path.isdir(packaged_candidate):
        return packaged_candidate

    project_root = os.path.dirname(here)
    dev_candidate = os.path.join(project_root, "build", "hf-cache")
    if os.path.isdir(dev_candidate):
        return dev_candidate

    return None


_DOCLING_WORKER_FLAG = "--docling-worker"


def _convert_via_docling_subprocess(path: str) -> str:
    """
    Converts `path` with docling in a child process (this same script, run
    with `_DOCLING_WORKER_FLAG`) and returns the Markdown. Raises on any
    failure, including the child being killed by a signal.

    docling's native libraries (PyTorch, onnxruntime) can crash with SIGSEGV,
    seen repeatedly on a 76-page document while the machine was short of
    memory. A crash like that is not a Python exception: run in-process it
    took the whole driver down, fallback code included, and the user got
    "Conversion failed" for a file markitdown could have converted.

    If this process is told to stop (the app stops a conversion on timeout
    or when its card is removed), the child is stopped first -- otherwise it
    would carry on converting, with its models in memory, for nobody.
    """
    import signal
    import subprocess

    child = subprocess.Popen(
        [sys.executable, "-B", os.path.abspath(__file__), _DOCLING_WORKER_FLAG, path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    def stop_child(signum, frame):  # type: ignore[no-untyped-def]
        child.kill()
        sys.exit(128 + signum)

    previous = {s: signal.signal(s, stop_child) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        stdout, _ = child.communicate()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    if child.returncode != 0:
        raise RuntimeError(f"docling worker exited with {child.returncode}")
    return json.loads(stdout)["markdown"]


def _docling_worker(path: str) -> int:
    """Child-process side of `_convert_via_docling_subprocess`: converts
    `path` and prints {"markdown": ...}. Any failure is a non-zero exit."""
    import threading
    import time

    parent = os.getppid()

    def exit_with_parent() -> None:
        # The parent's signal handler cannot run if it is killed outright.
        while os.getppid() == parent:
            time.sleep(1)
        os._exit(1)

    threading.Thread(target=exit_with_parent, daemon=True).start()

    try:
        markdown = _convert_via_docling(path)
    except Exception:
        return 1
    json.dump({"markdown": markdown}, sys.stdout, ensure_ascii=False)
    return 0


def _convert_via_docling(path: str) -> str:
    """
    Converts `path` with docling, returning the exported Markdown. Raises
    on any failure. Runs in the worker process (see `_docling_worker`).

    Sets up fully-offline operation (bundled model cache, no network
    fallback) and suppresses docling's/rapidocr's verbose logging before
    doing anything else, so a successful conversion prints nothing to
    stderr, consistent with this driver's stderr-for-fatal-errors-only
    contract.
    """
    if "HF_HOME" not in os.environ:
        cache_dir = _locate_model_cache_dir()
        if cache_dir:
            os.environ["HF_HOME"] = cache_dir
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")

    import contextlib
    import io
    import logging

    # Belt-and-suspenders: quiet the loggers we know about up front. Several
    # of docling's dependencies (notably rapidocr) construct their own
    # logger with an explicit INFO level lazily, inside convert() itself,
    # which can re-override a level set here before that import happens --
    # the redirect block below is what actually guarantees silence.
    for name in (
        "docling",
        "docling_core",
        "docling_ibm_models",
        "docling_parse",
        "rapidocr",
        "RapidOCR",
        "onnxruntime",
        "transformers",
        "huggingface_hub",
        "filelock",
        "PIL",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)

    from docling.document_converter import DocumentConverter

    # Redirect BOTH stderr and stdout for the duration of the conversion,
    # not just stderr: some of docling's internal components (e.g.
    # docling_ibm_models' table-matching post-processor) log WARNINGs
    # through Python's default `logging.lastResort` handler, which in this
    # process writes to stdout rather than stderr -- discovered when that
    # leaked straight into this driver's JSON-on-stdout output and broke
    # JSON parsing. Our own `json.dump(...)` call happens later in main(),
    # outside this block, so it's unaffected -- it's the only thing that
    # should ever reach the real stdout.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        converter = DocumentConverter()
        result = converter.convert(path)
        markdown = result.document.export_to_markdown()

    return markdown


# Plain-text formats whose bytes markitdown decodes by statistical guessing
# (charset_normalizer). HTML is not here: it went through the same tests and
# came out right with and without a charset declaration.
_LEGACY_CHARSET_EXTENSIONS = (".txt", ".text", ".md", ".markdown", ".csv")
_LEGACY_CHARSET_SAMPLE_BYTES = 1 << 20
_UNICODE_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
_HEBREW_POINT_PATTERN = re.compile(r"(?<![\u0591-\u05ea])[\u0591-\u05c7]")
_HEBREW_LATIN_ADJACENT_PATTERN = re.compile(r"[A-Za-z][א-ת]|[א-ת][A-Za-z]")
_HEBREW_FINAL_MIDWORD_PATTERN = re.compile(r"[ךםןףץ](?=[א-ת])")


def _detect_legacy_hebrew_charset(path: str) -> str | None:
    """
    Returns "cp1255" when a text file is Hebrew in the legacy Windows
    encoding (ISO-8859-8 text decodes identically for the letters), else
    None, which leaves the decision to markitdown's own detection.

    That detection is statistical and needs more text than a short file
    has: a 26-byte Hebrew file was decoded as Cyrillic (Windows-1251), with
    no warning, because the two encodings use the same byte range for their
    letters. The bytes alone cannot tell the two apart, so this asks whether
    the Windows-1255 reading is plausible *Hebrew*:

    - every byte must be defined in Windows-1255 (Russian text almost always
      contains bytes that are not);
    - nearly all non-ASCII characters must be Hebrew, and not glued to Latin
      letters (rules out Western European text, where an accented letter
      inside a Latin word would decode to a lone Hebrew letter);
    - no vowel point without a letter before it (Cyrillic capitals decode
      to points, at the start of words);
    - final-form letters almost never mid-word (Cyrillic к, н, п, у decode
      to ך, ם, ן, ף, which are everywhere inside Russian words).

    Files that are valid UTF-8, carry a Unicode byte-order mark, or contain
    NUL bytes (UTF-16/32) are never touched.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(_LEGACY_CHARSET_SAMPLE_BYTES)
    except OSError:
        return None
    return _legacy_hebrew_charset_of(data)


def _legacy_hebrew_charset_of(data: bytes) -> str | None:
    """The decision behind `_detect_legacy_hebrew_charset`, on bytes."""
    if not data or data.startswith(_UNICODE_BOMS) or b"\x00" in data:
        return None
    try:
        data.decode("utf-8")
        return None
    except UnicodeDecodeError as exc:
        # A sample that merely ends inside a multi-byte UTF-8 character.
        if len(data) == _LEGACY_CHARSET_SAMPLE_BYTES and exc.start >= len(data) - 4:
            return None
    try:
        text = data.decode("cp1255")
    except UnicodeDecodeError:
        return None

    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    hebrew = sum(1 for ch in text if _HEBREW_BLOCK_START <= ord(ch) <= _HEBREW_BLOCK_END)
    letters = sum(1 for ch in text if "א" <= ch <= "ת")
    if letters < 2 or hebrew < 0.8 * non_ascii:
        return None
    if _HEBREW_POINT_PATTERN.search(text):
        return None
    if len(_HEBREW_LATIN_ADJACENT_PATTERN.findall(text)) * 10 > letters:
        return None
    if len(_HEBREW_FINAL_MIDWORD_PATTERN.findall(text)) * 20 > letters:
        return None
    return "cp1255"


# Delimiters a ".csv" file may use instead of a comma: Excel writes
# semicolons under locales whose decimal separator is a comma, and its
# "Unicode Text" export is tab-separated UTF-16.
_CSV_ALTERNATIVE_DELIMITERS = (";", "\t", "|")
_CSV_SNIFF_ROWS = 50


def _csv_column_count(lines: list[str], delimiter: str) -> int:
    """
    The number of columns `lines` has when split on `delimiter`, or 0 when
    that delimiter does not describe the file: fewer than two columns, a
    header row of a different width, or under 90% of the rows agreeing.
    """
    import csv

    try:
        widths = [len(row) for row in csv.reader(lines, delimiter=delimiter) if row]
    except csv.Error:
        return 0
    if not widths:
        return 0
    common = max(set(widths), key=widths.count)
    if common < 2 or widths[0] != common or widths.count(common) < 0.9 * len(widths):
        return 0
    return common


def _decode_text_bytes(data: bytes) -> str | None:
    """Decodes a text file's bytes the way this driver reads text: Unicode
    by its byte-order mark, then strict UTF-8, then legacy Hebrew, then
    markitdown's own statistical detector."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if _legacy_hebrew_charset_of(data):
        return data.decode("cp1255")
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
    except Exception:
        return None
    return str(best) if best is not None else None


def _normalize_csv(path: str) -> bytes | None:
    """
    Returns the content of a CSV file re-written as comma-separated UTF-8
    when it uses another delimiter, else None (the file is passed to
    markitdown untouched). markitdown's CSV converter only splits on commas,
    so a semicolon- or tab-separated file came out as a one-column table with
    the separators left inside the cells.

    A file that reads consistently as comma-separated is never changed. For
    the others the delimiter must give every sampled row the same number of
    columns as the header (see `_csv_column_count`); when several qualify,
    the one giving the most columns wins.
    """
    import csv
    import io

    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    text = _decode_text_bytes(data)
    if not text:
        return None

    sample = [line for line in text.splitlines() if line.strip()][:_CSV_SNIFF_ROWS]
    if len(sample) < 2 or _csv_column_count(sample, ","):
        return None
    counts = {d: _csv_column_count(sample, d) for d in _CSV_ALTERNATIVE_DELIMITERS}
    delimiter = max(counts, key=lambda d: counts[d])
    if not counts[delimiter]:
        return None

    out = io.StringIO(newline="")
    try:
        csv.writer(out, lineterminator="\n").writerows(
            csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        )
    except csv.Error:
        return None
    return out.getvalue().encode("utf-8")


def _run_markitdown(path: str) -> str | None:
    """
    Converts `path` with the vendored `markitdown` package -- the original,
    unchanged conversion path this driver has always used. Used directly
    for non-PDF files and Hebrew-heavy PDFs, and as the fallback when
    `docling` fails on a non-Hebrew PDF.

    Returns the extracted text on success. On failure, prints a message to
    stderr and returns None (callers should treat that as the fatal-error
    exit path).
    """
    try:
        from markitdown import MarkItDown
    except Exception as exc:  # pragma: no cover - environment problem
        print(f"Failed to import markitdown: {exc}", file=sys.stderr)
        return None

    try:
        md = MarkItDown()
        charset = (
            _detect_legacy_hebrew_charset(path)
            if path.lower().endswith(_LEGACY_CHARSET_EXTENSIONS)
            else None
        )
        normalized_csv = _normalize_csv(path) if path.lower().endswith(".csv") else None
        if normalized_csv is not None:
            import io

            from markitdown import StreamInfo

            result = md.convert_stream(
                io.BytesIO(normalized_csv),
                stream_info=StreamInfo(extension=".csv", charset="utf-8"),
            )
        elif charset:
            from markitdown import StreamInfo

            result = md.convert(path, stream_info=StreamInfo(charset=charset))
        else:
            result = md.convert(path)
    except Exception as exc:
        print(f"Failed to convert '{path}': {exc}", file=sys.stderr)
        return None

    text = getattr(result, "text_content", None)
    if text is None:
        print(f"Conversion of '{path}' produced no text content.", file=sys.stderr)
        return None

    return text


# Extensions markitdown's AudioConverter accepts. Audio transcription was
# removed from the product: markitdown's only transcriber sends the recording
# to Google's cloud speech API, which contradicts the offline/private design.
# (The packages are also absent from the runtime, so audio inside a ZIP is
# skipped rather than uploaded; tests/regression_test.py guards that.)
_AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".mp4")


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == _DOCLING_WORKER_FLAG:
        return _docling_worker(sys.argv[2])

    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-file>", file=sys.stderr)
        return 2

    path = sys.argv[1]

    if path.lower().endswith(_AUDIO_EXTENSIONS):
        print(
            "Audio files are not supported. Transcription was removed because it "
            "required sending the recording to a cloud service, and this app works "
            "fully offline.",
            file=sys.stderr,
        )
        return 1

    text: str | None = None
    # True when the text was read from page images rather than from the PDF's
    # own text layer: docling produced it, and the sample of the text layer
    # (up to `_HEBREW_SAMPLE_MAX` pages) found next to no letters.
    ocr_derived = False

    use_docling = False
    alpha_count = 0
    if path.lower().endswith(".pdf"):
        hebrew_count, alpha_count = _sample_pdf_letters(path)
        hebrew_fraction = hebrew_count / alpha_count if alpha_count else 0.0
        use_docling = hebrew_fraction < _HEBREW_FRACTION_THRESHOLD

    if use_docling:
        try:
            text = _convert_via_docling_subprocess(path)
            ocr_derived = alpha_count < _HEBREW_MIN_ALPHA_CHARS
        except Exception:
            # docling failed (unsupported/corrupt PDF, missing models, a
            # native crash in its worker process, whatever) -- fall back to
            # markitdown below
            # rather than breaking a conversion that used to work. Not
            # treated as fatal, and deliberately not logged: only a total
            # failure (both engines) is worth telling the user about, via
            # the stderr message `_run_markitdown` prints if it also fails.
            text = None

    if text is None:
        text = _run_markitdown(path)
        if text is None:
            return 1

    # A UTF-8 byte-order mark at the start of a text file survives decoding
    # as an invisible U+FEFF character in front of the first word.
    text = text.lstrip("\ufeff")

    warnings, needs_confirmation = _validate(text, ocr_derived=ocr_derived)

    json.dump(
        {"markdown": text, "warnings": warnings, "needs_confirmation": needs_confirmation},
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

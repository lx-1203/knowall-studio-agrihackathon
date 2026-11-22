"""Standard Anki exports; safe native mathematics and Markdown answers."""
from contextlib import closing
import hashlib
from html import escape
import itertools
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import zipfile

import genanki
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.dollarmath.index import math_block_dollar
from app.config import settings

EXPORT_FORMAT_VERSION = 3


class _PersistedNote(genanki.Note):
    """The upstream writer owns schema/cards; supply only persisted seconds."""
    def write_to_db(self, cursor, timestamp, deck_id, id_gen):
        modified = getattr(self, 'export_mtime', None)
        if modified is not None:
            if type(modified) is not int or modified < 0 or modified > int(timestamp):
                raise ValueError('Invalid persisted Anki modification time')
            timestamp = modified
        return super().write_to_db(cursor, timestamp, deck_id, id_gen)


# Output policy only: upstream owns dollar parsing. Anki's default MathJax
# enables HTML/link/loading/macros, so unsupported controls stay visible code.
# A small mathematical vocabulary is deliberately conservative, not a TeX sandbox.
_MATH_CONTROLS = frozenset(r'''
frac dfrac tfrac sqrt overline underline vec hat widehat bar dot ddot tilde
widetilde alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta
iota kappa lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi
varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
sum prod int iint iiint oint lim log ln exp sin cos tan cot sec csc arcsin arccos
arctan sinh cosh tanh min max sup inf det gcd dim ker Pr partial nabla infty
pm mp times div cdot ast star circ bullet le leq ge geq ne neq approx sim simeq
equiv propto in notin subset supset subseteq supseteq cup cap setminus emptyset
forall exists neg land lor to rightarrow leftarrow Rightarrow Leftarrow
leftrightarrow Leftrightarrow mapsto implies iff ldots cdots vdots ddots
left right middle langle rangle lvert rvert lVert rVert vert Vert
text textrm mathrm mathbf mathit mathsf mathtt mathcal mathbb boldsymbol
operatorname begin end underbrace overbrace overset underset binom dbinom tbinom
quad qquad limits nolimits displaystyle textstyle scriptstyle scriptscriptstyle
'''.split()) | frozenset(['\\', ',', ';', ':', '!', ' ', '{', '}', '_', '%', '$', '#', '&', '|'])


def _anki_math(content: str, options: dict) -> str:
    display = options['display_mode']
    if any(match.group(1) not in _MATH_CONTROLS
           for match in re.finditer(r'\\([A-Za-z]+|[\s\S])', content)):
        marker = '$$' if display else '$'
        return '<code>' + escape(marker + content + marker) + '</code>'
    return ('\\[' if display else '\\(') + escape(content) + ('\\]' if display else '\\)')


def _math_block(state, start: int, end: int, silent: bool) -> bool:
    # Upstream recognizes delimiters; native getLines respects scoped quote/list
    # offsets, unlike the plugin's raw source slice (upstream issue 125).
    accepted = math_block_dollar(allow_labels=False, allow_blank_lines=False)(
        state, start, end, silent)
    if accepted and not silent:
        state.tokens[-1].content = state.getLines(start, state.line, state.blkIndent, False).strip()[2:-2]
    return accepted


def _html(text: str, *, markdown: bool = False, math: bool = False) -> str:
    # Anki separates HTML fields with U+001F. Keep ordinary fronts literal.
    text = text.replace("\x1f", "�").replace("\x00", "�")
    if markdown or math:
        # Each render owns mutable native parser/rule state. Native tokens decide
        # whether a front needs conversion; no separate dollar/cloze parser.
        parser = MarkdownIt('js-default', {'html': False, 'breaks': True}).disable('image')
        parser.use(dollarmath_plugin, allow_labels=False, allow_space=False,
                   allow_digits=False, allow_blank_lines=False, double_inline=True,
                   renderer=_anki_math)
        parser.block.ruler.at('math_block', _math_block)
        env = {}
        tokens = parser.parse(text, env)
        has_math = any(token.type.startswith('math_')
                       for parent in tokens for token in [parent, *(parent.children or [])])
        if markdown or has_math:
            return parser.renderer.render(tokens, parser.options, env)
    return escape(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def export_apkg(cards: list[dict], deck_name: str, output_path: str | None = None,
                *, deck_id: str | None = None) -> str:
    """Export persisted IDs when supplied; legacy callers retain the three-arg API.

    Without persisted card IDs, identity falls back to front/type/occurrence;
    editing a legacy front cannot be identified as the same note. Anki's default
    import only updates newer notes; even Always skips equal modification times.
    """
    identity = str(deck_id) if deck_id is not None else deck_name
    fingerprint = hashlib.sha256(('knowall-deck-v1:' + identity).encode()).digest()[:6]
    numeric_id = int.from_bytes(fingerprint, 'big') or 1
    # Anki resolves decks by name, even when numeric IDs differ.
    name = f'{deck_name} [{fingerprint.hex()}]' if deck_id is not None else deck_name
    deck = genanki.Deck(numeric_id, name)
    occurrences: dict[tuple[str, str], int] = {}
    for card in cards:
        front, back = card.get('front', ''), card.get('back', '')
        kind = card.get('card_type', 'qa')
        fields = [_html(front, math=True), _html(back, markdown=True)]
        tags = list(dict.fromkeys('_'.join(str(t).replace('\x00', '').split())
                                  for t in (card.get('tags') or [])))
        tags = [t for t in tags if t]
        key = (front, kind)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        note_id = card.get('id')
        guid = (genanki.guid_for('knowall-note-v1', identity, str(note_id))
                if note_id is not None else
                genanki.guid_for('knowall-legacy-note-v1', identity, front, kind, occurrence))
        model = genanki.CLOZE_MODEL if kind == 'cloze' else genanki.BASIC_MODEL
        note = _PersistedNote(model=model, fields=fields, tags=tags, guid=guid)
        if kind == 'cloze' and not note.cards:
            # Old malformed cloze notes must remain visible instead of vanishing.
            note = _PersistedNote(model=genanki.BASIC_MODEL, fields=fields, tags=tags, guid=guid)
        note.export_mtime = card.get("anki_export_mtime")
        deck.add_note(note)

    owns_output = output_path is None
    if owns_output:
        directory = Path(settings.export_dir) / 'anki_cards'
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, output_path = tempfile.mkstemp(prefix='anki_', suffix='.apkg', dir=directory)
        os.close(descriptor)
    try:
        # Upstream write_to_file leaves its temp DB. Keep its complete schema/data
        # generator and use stdlib lifetimes for the SQLite/ZIP container instead.
        with tempfile.TemporaryDirectory(prefix='knowall-anki-') as temporary:
            database = Path(temporary) / 'collection.anki2'
            timestamp = time.time()
            with closing(sqlite3.connect(database)) as conn:
                with conn:
                    genanki.Package(deck).write_to_db(
                        conn.cursor(), timestamp, itertools.count(int(timestamp * 1000)))
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.write(database, 'collection.anki2')
                archive.writestr('media', '{}')
    except Exception:
        if owns_output:
            Path(output_path).unlink(missing_ok=True)
        raise
    return str(output_path)

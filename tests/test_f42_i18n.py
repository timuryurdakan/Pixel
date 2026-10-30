"""F4-2: the English interface stays complete (docs/ARASTIRMA2_ORTAK.md, web/js/i18n.js).

Turkish is the source text and the key. Every `tt(...)` call in web/js whose argument is a string literal (or literals
joined with +) must have an English entry in web/js/i18n_en.js; the table has no duplicate keys; and the browser
module-load suite (tests/js/modules_load.test.js) lists every library module, so a syntax error cannot hide in a file
no other suite imports.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "web" / "js"
ENTRY_POINTS = {"main.js", "guide.js", "tanitim.js", "sprite-preview.js"}
_ESC = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}


def _literal(src: str, i: int):
    """The JS string literal starting at src[i] (a quote) → (value, index after it)."""
    q = src[i]
    out, i = [], i + 1
    while src[i] != q:
        if src[i] == "\\":
            out.append(_ESC.get(src[i + 1], src[i + 1]))
            i += 2
        else:
            out.append(src[i])
            i += 1
    return "".join(out), i + 1


def english_table():
    """[(key, value)] in file order from i18n_en.js."""
    src = (JS / "i18n_en.js").read_text(encoding="utf-8")
    body = src[src.index("export const EN = {"):]
    pairs, i = [], body.index("{") + 1
    while True:
        m = re.compile(r"\s*(?://[^\n]*\s*)*(['\"]|})").match(body, i)
        if not m or m.group(1) == "}":
            return pairs
        key, i = _literal(body, m.start(1))
        m2 = re.compile(r"\s*:\s*(['\"])").match(body, i)
        value, i = _literal(body, m2.start(1))
        pairs.append((key, value))
        i = re.compile(r"\s*,?").match(body, i).end()


def translated_calls():
    """{(file, text)} for every tt(<literal> [+ <literal>]*) call."""
    found = set()
    for path in JS.glob("*.js"):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?<![\w$.])tt\(\s*(['\"])", src):
            parts, i = [], m.start(1)
            while True:
                value, i = _literal(src, i)
                parts.append(value)
                nxt = re.compile(r"\s*\+\s*(['\"])").match(src, i)
                if not nxt:
                    break
                i = nxt.start(1)
            found.add((path.name, "".join(parts)))
    return found


class EnglishTableTests(unittest.TestCase):
    def test_no_duplicate_keys(self):
        keys = [k for k, _ in english_table()]
        dups = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(dups, [])
        self.assertGreater(len(keys), 500)

    def test_every_translated_literal_has_an_english_entry(self):
        table = dict(english_table())
        missing = sorted((f, s) for f, s in translated_calls() if s not in table)
        self.assertEqual(missing, [], "\n".join("%s: %r" % m for m in missing))

    def test_no_markup_in_english_texts(self):
        for k, v in english_table():
            self.assertNotRegex(v, r"<[a-zA-Z/!]", k)


class ModuleLoadListTests(unittest.TestCase):
    def test_the_browser_load_suite_lists_every_library_module(self):
        listed = set(re.findall(r"'([\w-]+\.js)'", (ROOT / "tests" / "js" / "modules_load.test.js").read_text()))
        modules = {p.name for p in JS.glob("*.js")} - ENTRY_POINTS
        self.assertEqual(sorted(modules - listed), [])
        self.assertEqual(sorted(listed - modules), [])


if __name__ == "__main__":
    unittest.main()

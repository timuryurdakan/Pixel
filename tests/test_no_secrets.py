"""No real credential is ever committed (2026-11-01 security and cost check, docs/SECURITY_AUDIT.md §20).

Scans every file of the project for the shapes of provider API keys, tokens and private keys, in code and comments
alike: a key pasted into a comment of a public repository is found by scrapers within minutes. Tests use fake keys
that carry the word CANARY (or are made of one repeated character), which this scan allows.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".wrangler", ".vercel", ".venv", "venv"}
MAX_BYTES = 2 * 1024 * 1024

PATTERNS = {
    "OpenAI / DeepSeek style key": r"sk-[A-Za-z0-9_-]{20,}",
    "Anthropic key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "Google API key": r"AIza[0-9A-Za-z_-]{35}",
    "Groq key": r"gsk_[A-Za-z0-9]{20,}",
    "GitHub token": r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}",
    "Slack token": r"xox[abpr]-[A-Za-z0-9-]{10,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "Hugging Face token": r"hf_[A-Za-z0-9]{30,}",
    "Replicate token": r"r8_[A-Za-z0-9]{30,}",
    "Private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "JSON web token": r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}",
}
FOUND = re.compile("|".join("(?:%s)" % p for p in PATTERNS.values()))


def allowed(match: str) -> bool:
    """Test fakes: marked CANARY, or one character repeated (sk-aaaa…)."""
    if "CANARY" in match.upper():
        return True
    body = re.sub(r"^[A-Za-z0-9]{1,6}[-_]", "", match)
    return len(set(body)) <= 2


def project_files():
    for p in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts) or not p.is_file():
            continue
        if p.stat().st_size <= MAX_BYTES:
            yield p


class NoSecretsTests(unittest.TestCase):
    def test_no_credential_shapes_in_any_project_file(self):
        hits = []
        for p in project_files():
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue                      # binary assets (sprites, fonts) cannot hold a pasted key as text
            for m in FOUND.finditer(text):
                if not allowed(m.group(0)):
                    line = text.count("\n", 0, m.start()) + 1
                    hits.append("%s:%d: %s…" % (p.relative_to(ROOT), line, m.group(0)[:8]))
        self.assertEqual(hits, [], "a credential-shaped string is in the project; remove it and rotate the key")

    def test_the_scan_finds_a_key_in_a_comment(self):
        fake = "# eski anahtar: " + "sk-" + "proj" + "Q7xW2mN9vB4kL8pR3tY6zH1cF5dG0jS"
        self.assertTrue(FOUND.search(fake))
        self.assertFalse(allowed(FOUND.search(fake).group(0)))
        self.assertTrue(allowed("sk-CANARY-f43b-0123456789-SECRET"))
        self.assertTrue(FOUND.search("const k = 'AIza" + "SyD3x9Qw7Lm2Np8Rt5Vb1Kc4Hj6Gf0Zs3Ae';"))

    def test_secret_files_stay_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        for rule in (".env", ".env.*", "*.pem", "*.key", ".npmrc", ".dev.vars", ".vercel/"):
            self.assertIn(rule, ignored)


if __name__ == "__main__":
    unittest.main()

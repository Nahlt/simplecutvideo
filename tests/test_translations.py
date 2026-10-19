import re
import string
from pathlib import Path

from simplecutvideo.translations import STRINGS

SRC = Path(__file__).resolve().parents[1] / "simplecutvideo"


def _code() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")
                     if p.name != "translations.py")


def _fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_all_languages_have_same_keys_and_placeholders():
    reference = STRINGS["it"]
    for lang, table in STRINGS.items():
        assert set(table) == set(reference), f"{lang}: {set(table) ^ set(reference)}"
        for key, text in table.items():
            assert _fields(text) == _fields(reference[key]), f"{lang}.{key}"


def test_every_key_used_in_code_exists_and_none_unused():
    code = _code()
    used = set(re.findall(r"""tr\(\s*["']([a-z0-9_]+)["']""", code))
    used |= set(re.findall(r"""["']((?:sc|qs|err|notice|hdr)_[a-z0-9_]+)["']""", code))
    missing = used - set(STRINGS["it"])
    assert not missing, missing
    unused = {k for k in STRINGS["it"] if k not in code}
    assert not unused, unused

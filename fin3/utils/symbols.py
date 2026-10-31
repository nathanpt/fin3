"""Symbol-list text parsing shared by scripts and manifest selectors."""

from __future__ import annotations


def parse_symbols(text: str) -> list[str]:
    """Parse symbols from newline- and/or comma-separated text.

    Blank lines and ``#`` comments are ignored. Whitespace is trimmed.
    Symbols keep their original case — callers that need uppercase text
    (e.g. the bulk-download script) pass ``text.upper()``.

    Returns
    -------
    list[str]
        Symbols in first-occurrence order with duplicates dropped.
    """
    symbols: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        for tok in line.split(","):
            tok = tok.strip()
            if tok:
                symbols.append(tok)
    # Preserve order, drop duplicates
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out

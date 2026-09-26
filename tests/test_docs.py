"""
Documentation that contradicts itself, caught by the build.

Counts, cookbook coverage and the MCP topic list are guarded in
test_cookbook.py. These guard the *prose* - the kind of staleness that survived
longest in this repository:

  * handoff.md opened with "Phase 4 is under way, 4.3 is next" for several
    releases after Phase 4 shipped;
  * docs/README.md called an implemented design record "not implemented";
  * a checkbox in a finished phase can quietly stay open, which is how the
    Phase 2 "orphaned outputs" item disappeared without a trace.

A link checker was run by hand after every change for months; it lives here now.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
ROADMAP = DOCS / "roadmap.md"
HANDOFF = DOCS / "handoff.md"
INDEX = DOCS / "README.md"

LINKED_FILES = sorted(DOCS.glob("**/*.md")) + [
    REPO / "README.md",
    REPO / "AGENTS.md",
    REPO / "notebooks" / "README.md",
]

PHASES_COMPLETE = re.compile(r"Phases 0–(\d+)\**,? (?:are )?complete")
PHASE_HEADER = re.compile(r"^## Phase (\d+(?:\.\d+)?) — (.*)$", re.M)


def without_code(text: str) -> str:
    """Fenced blocks hold regexes and shell, which look like links and are not."""
    return re.sub(r"```.*?```", "", text, flags=re.S)


# ==============================================================================
# Links
# ==============================================================================

def test_every_relative_link_resolves():
    broken = []
    for path in LINKED_FILES:
        for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", without_code(path.read_text())):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            # `file.py:133` points at a line; the file is what must exist.
            relative = target.split("#")[0].split(":")[0]
            if relative and not (path.parent / relative).resolve().exists():
                broken.append(f"{path.relative_to(REPO)} -> {target}")

    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)


# ==============================================================================
# The roadmap agrees with itself, and the handoff agrees with the roadmap
# ==============================================================================

def completed_through(text: str, name: str) -> int:
    claims = {int(n) for n in PHASES_COMPLETE.findall(text)}
    assert claims, f"{name} no longer states which phases are complete"
    assert len(claims) == 1, f"{name} states different completed phases in different places: {sorted(claims)}"
    return claims.pop()


def phase_headers():
    return [(float(number), title) for number, title in PHASE_HEADER.findall(ROADMAP.read_text())]


def test_current_position_matches_the_phase_headers():
    """'Phases 0–N complete' means exactly the phases up to N carry a tick."""
    done_through = completed_through(ROADMAP.read_text(), "roadmap.md")
    headers = phase_headers()
    assert headers, "the extractor should find the phase headers"

    for number, title in headers:
        ticked = title.rstrip().endswith("✅")
        if number <= done_through:
            assert ticked, f"Phase {number:g} is within 'Phases 0–{done_through} complete' but not ticked"
        else:
            assert not ticked, f"Phase {number:g} is ticked, but the current position stops at {done_through}"


def test_the_handoff_agrees_with_the_roadmap():
    assert completed_through(HANDOFF.read_text(), "handoff.md") == completed_through(
        ROADMAP.read_text(), "roadmap.md"
    )


def test_an_open_box_in_a_finished_phase_says_why():
    """
    A ticked phase with an unticked item is either unfinished or has an item
    that was deferred or dropped. The second is fine - if the line says so.
    """
    text = ROADMAP.read_text()
    sections = re.split(r"(?=^## )", text, flags=re.M)
    unexplained = []

    for section in sections:
        header = section.splitlines()[0] if section else ""
        if not (header.startswith("## Phase") and header.rstrip().endswith("✅")):
            continue
        for line in section.splitlines():
            if line.lstrip().startswith("- [ ]"):
                if not re.search(r"~~|deferred|dropped", line, re.I):
                    unexplained.append(f"{header.strip()}: {line.strip()}")

    assert not unexplained, "open items in finished phases, with no reason given:\n  " + "\n  ".join(unexplained)


# ==============================================================================
# Design records: what each says about itself, and what the index says
# ==============================================================================

def implemented(status: str) -> bool:
    lowered = status.lower()
    return "implemented" in lowered and not re.search(r"\b(not|nothing) implemented", lowered)


def test_each_design_record_status_agrees_with_the_index():
    index = INDEX.read_text()
    disagreements = []

    for record in sorted((DOCS / "design").glob("[0-9][0-9][0-9]-*.md")):
        own = re.search(r"^\*\*Status:\*\*(.*)$", record.read_text(), re.M)
        assert own, f"{record.name} has no **Status:** line"

        row = re.search(rf"^\|[^|]*\|[^|]*\(design/{re.escape(record.name)}\)[^|]*\|([^|]*)\|", index, re.M)
        assert row, f"{record.name} has no row in docs/README.md"

        if implemented(own.group(1)) != implemented(row.group(1)):
            disagreements.append(
                f"{record.name}: record says '{own.group(1).strip()}', index says '{row.group(1).strip()}'"
            )

    assert not disagreements, "\n".join(disagreements)

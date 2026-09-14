"""
Authoring guidance, served as a tool rather than a resource.

The distinction is the whole point. MCP resources have to be explicitly
fetched, and many clients never surface them prominently — a resource the agent
does not read is dead weight. Tools get called, especially when the server's
`instructions` say to call them. So the guidance that decides whether generated
code uses a real API lives here, not in a resource nobody opens.

Named `authoring`, not `cookbook`: the package exports a `cookbook()` function,
and a module of the same name would be shadowed by it - `from . import cookbook`
would quietly hand you the function.

Content comes from `docs/cookbook.md`, whose every snippet is executed by the
test suite. The API surface is generated from the package at call time, so it
cannot drift from what is actually exported.
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Beside this module in an installed wheel (hatchling force-includes it there),
#: or in `docs/` when running from a checkout. Without the force-include a
#: `pip install` user would get the tool but never the content — which is how
#: this was found.
_PACKAGED = Path(__file__).resolve().parent / "cookbook.md"
_IN_CHECKOUT = Path(__file__).resolve().parents[2] / "docs" / "cookbook.md"

COOKBOOK_PATH = _PACKAGED if _PACKAGED.is_file() else _IN_CHECKOUT

#: Topics whose section is returned in full when no topic is requested. The
#: rest are listed by name so the agent knows what it can ask for.
ALWAYS_INCLUDED = ("quickstart", "pitfalls")


def _sections() -> Dict[str, str]:
    """Split the cookbook into `## topic` sections, preserving order."""
    try:
        text = COOKBOOK_PATH.read_text(encoding="utf-8")
    except OSError:
        # Installed without docs/, or a packaging slip. Say so rather than
        # silently serving nothing.
        return {}

    parts = re.split(r"^## ([a-z-]+)$", text, flags=re.M)
    # parts[0] is the preamble; then alternating (name, body).
    return {
        name: body.strip()
        for name, body in zip(parts[1::2], parts[2::2])
    }


def topics() -> List[str]:
    return list(_sections())


def api_surface() -> List[Dict[str, str]]:
    """
    Every public name, with the first line of its docstring.

    Generated rather than written down: a new export shows up here immediately,
    and nothing here can describe something that no longer exists.
    """
    import smartmdao

    surface = []
    for name in sorted(smartmdao.__all__):
        value = getattr(smartmdao, name, None)

        if isinstance(value, (str, int, float, bool)):
            # A module-level constant. Its type's docstring ("str(object='')
            # -> str") is noise; the value is the useful thing.
            surface.append(
                {"name": name, "kind": "constant", "summary": f"= {value!r}"}
            )
            continue

        doc = (getattr(value, "__doc__", None) or "").strip()
        summary = next((line.strip() for line in doc.splitlines() if line.strip()), "")
        surface.append(
            {
                "name": name,
                "kind": "class" if isinstance(value, type) else type(value).__name__,
                "summary": summary[:160],
            }
        )
    return surface


def cookbook(topic: Optional[str] = None) -> Dict[str, Any]:
    """
    Guidance for writing SmartMDAO pipelines.

    Without a topic, returns the essentials plus the list of what else can be
    asked for. With one, returns that section in full.
    """
    sections = _sections()

    if not sections:
        return {
            "ok": False,
            "error": (
                "docs/cookbook.md is not available in this installation. "
                "See https://github.com/wghami/SmartMDAO/blob/main/docs/cookbook.md"
            ),
        }

    if topic is not None:
        key = topic.strip().lower()
        if key not in sections:
            return {
                "ok": False,
                "error": f"No cookbook topic {topic!r}.",
                "topics": list(sections),
            }
        return {
            "ok": True,
            "topic": key,
            "guidance": sections[key],
            "topics": list(sections),
        }

    included = {
        name: sections[name] for name in ALWAYS_INCLUDED if name in sections
    }
    return {
        "ok": True,
        "topic": None,
        "guidance": included,
        "topics": list(sections),
        "api_surface": api_surface(),
        "note": (
            "Ask for a specific topic to get its section in full. Every snippet "
            "in this guidance is executed by the test suite, so it reflects the "
            "installed version rather than recollection."
        ),
    }

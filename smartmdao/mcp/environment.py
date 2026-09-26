"""
Which Python a pipeline file belongs to. See docs/design/007.

The server used to analyse and run every file with its own interpreter, so a
pipeline importing a library the server's environment lacked could not even be
analysed, and one that did import was described by the server's SmartMDAO
rather than the version the project pins. This module decides which
environment a file should be handled in - and says so, every time.

Precedence, highest first:

    explicit    python=   that executable
    project     project=  <project>/.venv
    discovered            nearest ancestor holding pyproject.toml AND .venv
    server                the server's own interpreter, as before

Nothing else is guessed: not VIRTUAL_ENV, not conda, not whatever `python` is
on PATH.
"""
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Tuple, Union

EXPLICIT = "explicit"
PROJECT = "project"
DISCOVERED = "discovered"
SERVER = "server"

#: The oldest SmartMDAO a project may pin and still be analysed or run for it.
#: A protocol match says two processes can talk; it does not say the older
#: version's answers are worth having. Raise this whenever a fix lands that
#: makes older answers WRONG rather than merely poorer, and add the reason
#: below - the refusal quotes it.
MIN_PROJECT_VERSION = "1.26.0"
FLOOR_REASON = "1.26.0 is the first release with the worker this server talks to"


@dataclass(frozen=True)
class Interpreter:
    """Where a file is handled, and how that was decided."""
    path: str
    environment: str
    source: str
    smartmdao: Optional[str] = None
    note: Optional[str] = None

    @property
    def is_server(self) -> bool:
        """
        Whether this is the server's own environment - decided by comparing
        environment DIRECTORIES. The executable in a venv is normally a symlink
        to a shared base interpreter, so resolving it would call two different
        projects' environments the same one.
        """
        return _same_directory(self.environment, sys.prefix)

    def describe(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _same_directory(left: str, right: str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:                                   # pragma: no cover - unresolvable path
        return False


def venv_python(environment: Path) -> Optional[Path]:
    """The interpreter inside a virtual environment, on POSIX or Windows."""
    for candidate in (environment / "bin" / "python", environment / "Scripts" / "python.exe"):
        if candidate.is_file():
            return candidate
    return None


def environment_of(executable: Path) -> Path:
    """
    The environment an executable belongs to: `<env>/bin/python` -> `<env>`.

    Deliberately not resolved - resolving a venv's symlinked interpreter lands
    in the shared base installation, not in the venv.
    """
    return Path(os.path.abspath(executable)).parent.parent


_DIST_INFO = re.compile(r"^smartmdao-([^-]+)\.dist-info$", re.I)


def installed_version(environment: Path) -> Optional[str]:
    """
    SmartMDAO's version in `environment`, read from its dist-info directory.

    Static on purpose: starting that environment's Python costs most of a
    second, and this is asked before every call that leaves the server.
    """
    for site in list(environment.glob("lib/python*/site-packages")) + [environment / "Lib" / "site-packages"]:
        if not site.is_dir():
            continue
        for entry in site.iterdir():
            match = _DIST_INFO.match(entry.name)
            if match:
                return match.group(1)
    return None


def queried_version(executable: Path, timeout: float = 30.0) -> Optional[str]:
    """
    SmartMDAO's version as `executable` reports it - for an interpreter outside
    a venv, where there is no dist-info directory to read. Reads metadata only;
    SmartMDAO itself is not imported.
    """
    try:
        completed = subprocess.run(
            [str(executable), "-c",
             "import importlib.metadata as m\n"
             "try: print(m.version('smartmdao'))\n"
             "except m.PackageNotFoundError: pass"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() or None


def version_tuple(version: str) -> Tuple[int, ...]:
    """'1.26.0' -> (1, 26, 0); anything after the numbers is ignored."""
    return tuple(int(part) for part in re.findall(r"\d+", version.split("+")[0])[:3])


def _server() -> Interpreter:
    from importlib.metadata import PackageNotFoundError, version

    try:
        own = version("smartmdao")
    except PackageNotFoundError:                     # pragma: no cover - running from a bare checkout
        own = None
    return Interpreter(path=sys.executable, environment=sys.prefix, source=SERVER, smartmdao=own)


def _discover(file: Path) -> Optional[Path]:
    """The nearest ancestor of `file` with both a pyproject.toml and a .venv."""
    for directory in [file.parent, *file.parent.parents]:
        if (directory / "pyproject.toml").is_file() and venv_python(directory / ".venv"):
            return directory / ".venv"
    return None


def resolve(
    file: Union[str, Path],
    python: Optional[str] = None,
    project: Optional[str] = None,
) -> Tuple[Optional[Interpreter], Optional[dict]]:
    """
    The interpreter for `file`, or a refusal saying why there is none.

    Returns `(interpreter, None)` or `(None, {"ok": False, "refused": ..., "error": ...})`.
    """
    if python is not None:
        executable = Path(python).expanduser()
        if not executable.is_file():
            return None, _refusal("interpreter", f"No such interpreter: {executable}")
        environment = environment_of(executable)
        version = installed_version(environment) or queried_version(executable)
        return _checked(Interpreter(str(executable), str(environment), EXPLICIT, version), strict=True)

    if project is not None:
        environment = Path(project).expanduser().resolve() / ".venv"
        executable = venv_python(environment)
        if executable is None:
            return None, _refusal(
                "interpreter",
                f"{Path(project).expanduser()} has no .venv. Create it (for example `uv sync` "
                f"there), or pass python= with the interpreter to use.",
            )
        return _checked(
            Interpreter(str(executable), str(environment), PROJECT, installed_version(environment)),
            strict=True,
        )

    found = _discover(Path(file).expanduser().resolve())
    if found is not None:
        interpreter = Interpreter(str(venv_python(found)), str(found), DISCOVERED, installed_version(found))
        return _checked(interpreter, strict=False)

    return _server(), None


def _checked(interpreter: Interpreter, strict: bool) -> Tuple[Optional[Interpreter], Optional[dict]]:
    """
    Refuses an environment with no SmartMDAO, or one below the floor - when the
    caller named it (`strict`). A *discovered* environment that fails either
    test falls back to the server's own instead, with a note saying why: a
    project that works today, because the server's environment happens to hold
    its dependencies or because it pins an older SmartMDAO, must not break the
    day the server is upgraded. Refusing is reserved for when the caller said
    which environment to use, where a substitution would be the guess 003
    forbids.
    """
    if interpreter.is_server:
        return _server(), None

    problem = None
    if interpreter.smartmdao is None:
        problem = f"{interpreter.environment} has no SmartMDAO"
    elif version_tuple(interpreter.smartmdao) < version_tuple(MIN_PROJECT_VERSION):
        problem = (f"{interpreter.environment} has SmartMDAO {interpreter.smartmdao}, below "
                   f"{MIN_PROJECT_VERSION} ({FLOOR_REASON})")

    if problem and not strict:
        server = _server()
        return Interpreter(
            server.path, server.environment, SERVER, server.smartmdao,
            note=f"{problem}, so the server's own environment was used. Install or upgrade "
                 f"smartmdao>={MIN_PROJECT_VERSION} there to have the project's environment used.",
        ), None

    if interpreter.smartmdao is None:
        return None, _refusal(
            "no-smartmdao",
            f"The environment at {interpreter.environment} has no SmartMDAO. Install it there "
            f"(`uv add 'smartmdao>={MIN_PROJECT_VERSION}'`), or leave out python= and project= "
            f"to use the server's own environment.",
            interpreter,
        )

    if version_tuple(interpreter.smartmdao) < version_tuple(MIN_PROJECT_VERSION):
        return None, _refusal(
            "version",
            f"The environment at {interpreter.environment} has SmartMDAO {interpreter.smartmdao}; "
            f"this server needs >= {MIN_PROJECT_VERSION} there ({FLOOR_REASON}). Upgrade it with "
            f"`uv add 'smartmdao>={MIN_PROJECT_VERSION}'`.",
            interpreter,
        )
    return interpreter, None


def _refusal(kind: str, message: str, interpreter: Optional[Interpreter] = None) -> dict:
    refusal = {"ok": False, "refused": kind, "error": message}
    if interpreter is not None:
        refusal["interpreter"] = interpreter.describe()
    return refusal

"""Licence texts for the built app, collected at build time into its `licenses/` folder.

MIT, BSD and Apache licences ask that their copyright and licence text travel with every binary
copy, and the app carries about 60 Python packages plus its own Python. So the build copies:

- licenses/python-packages/<name>-<version>/  every runtime package's own licence files
  (LICENSE, COPYING, NOTICE, AUTHORS from its .dist-info; for the few that ship none, the text
  in packaging/package-licenses), found by walking Watt Disco's
  dependencies for the platform being built, so build-only tools (PyInstaller, Pillow, pytest)
  are left out
- licenses/python/LICENSE.txt   the Python runtime's licence (PSF)
- licenses/native/              the C libraries inside that runtime (packaging/native-licenses)

`python packaging/licenses.py` lists what would be copied, and fails when a package has no licence file.
"""

import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

HERE = Path(__file__).parent
LICENCE_WORDS = ("LICEN", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT")
ROOT_PACKAGE = "wattdisco"


def _norm(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def runtime_distributions(root: str = ROOT_PACKAGE) -> list[metadata.Distribution]:
    """The installed distributions the app needs to run here, from `root`'s requirements (with extras)."""
    seen: dict[str, metadata.Distribution] = {}
    todo: list[tuple[str, set[str]]] = [(root, set())]
    while todo:
        name, extras = todo.pop()
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        key = _norm(dist.metadata["Name"])
        first = key not in seen
        seen[key] = dist
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker and not any(req.marker.evaluate({"extra": e}) for e in (extras or {""})):
                continue
            if first or req.extras:
                todo.append((req.name, set(req.extras)))
    seen.pop(_norm(root), None)  # Watt Disco's own licence ships at the top level
    return sorted(seen.values(), key=lambda d: _norm(d.metadata["Name"]))


def licence_files(dist: metadata.Distribution) -> list[Path]:
    """The licence files a distribution ships in its metadata folder."""
    files = []
    for f in dist.files or []:
        parts = Path(str(f)).parts
        if not parts or not parts[0].endswith(".dist-info"):
            continue
        if any(word in Path(str(f)).name.upper() for word in LICENCE_WORDS):
            path = Path(dist.locate_file(f))
            if path.is_file():
                files.append(path)
    return files


def fallback_licence(dist: metadata.Distribution) -> Path | None:
    """A committed text for a package that ships no licence file (packaging/package-licenses).

    The pyobjc packages share one licence, which pyobjc-framework-Cocoa ships and the others leave out.
    """
    name = _norm(dist.metadata["Name"])
    for candidate in (name, "pyobjc" if name.startswith("pyobjc") else None):
        if candidate and (HERE / "package-licenses" / f"{candidate}.txt").is_file():
            return HERE / "package-licenses" / f"{candidate}.txt"
    return None


def python_licence() -> Path:
    base = Path(sys.base_prefix)
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for candidate in (base / "lib" / version / "LICENSE.txt", base / "LICENSE.txt", base / "LICENSE",
                      base / "Lib" / "LICENSE.txt"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no Python licence file under {base}")


def bundled_distributions(modules: set[str]) -> list[metadata.Distribution]:
    """The distributions that provide these top-level modules (what PyInstaller actually bundled)."""
    owners = metadata.packages_distributions()
    names = {name for module in modules for name in owners.get(module, [])}
    dists = {}
    for name in names:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        dists[_norm(dist.metadata["Name"])] = dist
    dists.pop(_norm(ROOT_PACKAGE), None)
    return list(dists.values())


def datas(modules: set[str] = frozenset()) -> list[tuple[str, str]]:
    """(source file, folder inside the app) pairs for PyInstaller's `datas`.

    `modules` are top-level module names in the build; their packages get a licence folder too,
    so nothing PyInstaller pulls in on its own is left out.
    """
    out, missing = [], []
    dists = {_norm(d.metadata["Name"]): d for d in runtime_distributions() + bundled_distributions(set(modules))}
    for dist in sorted(dists.values(), key=lambda d: _norm(d.metadata["Name"])):
        folder = f"licenses/python-packages/{_norm(dist.metadata['Name'])}-{dist.version}"
        files = licence_files(dist)
        if not files and (fallback := fallback_licence(dist)):
            files = [fallback]
        if not files:
            missing.append(dist.metadata["Name"])
        out += [(str(f), folder) for f in files]
    if missing:
        raise RuntimeError(f"no licence file found for: {', '.join(missing)}")
    out.append((str(python_licence()), "licenses/python"))
    out += [(str(f), "licenses/native") for f in sorted((HERE / "native-licenses").glob("*")) if f.is_file()]
    return out


if __name__ == "__main__":
    for source, folder in datas():
        print(f"{folder}/{Path(source).name}")

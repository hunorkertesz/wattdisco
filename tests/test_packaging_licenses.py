import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import licenses  # noqa: E402


def test_every_runtime_package_has_a_licence_text():
    pairs = licenses.datas()
    folders = {folder for _, folder in pairs}
    for dist in licenses.runtime_distributions():
        name = f"licenses/python-packages/{licenses._norm(dist.metadata['Name'])}-{dist.version}"
        assert name in folders, dist.metadata["Name"]
    assert "licenses/python" in folders and "licenses/native" in folders
    assert all(Path(source).is_file() for source, _ in pairs)
    names = {licenses._norm(d.metadata["Name"]) for d in licenses.runtime_distributions()}
    assert {"fastapi", "bleak", "stravalib", "uvicorn"} <= names
    assert not names & {"pyinstaller", "pillow", "pytest", "wattdisco"}  # build tools and the app itself stay out

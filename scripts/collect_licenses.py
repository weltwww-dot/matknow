"""Include installed runtime distributions' license files in the desktop bundle."""

import sys
from importlib.metadata import distribution
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sections = ["MatKnow third-party notices\nQt/PySide6 and Shiboken are dynamically linked open-source dependencies.\nQt source and license information: https://code.qt.io/ and https://doc.qt.io/qtforpython-6/licenses.html\n"]
for name in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt", "GPL-2.0-only.txt"):
    path = root / "licenses" / name
    if not path.is_file():
        raise SystemExit(f"Required open-source license text is missing: {name}")
    sections.append(f"\n--- {name} ---\n" + path.read_text(encoding="utf-8"))
for path in sorted((root / "licenses").glob("Qt-*.txt")):
    sections.append(f"\n--- {path.name} ---\n" + path.read_text(encoding="utf-8"))
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    sections.append("\n--- Python runtime LICENSE.txt ---\n" + python_license.read_text(encoding="utf-8"))
for name in ("PySide6-Essentials", "shiboken6", "pypdf", "python-docx", "openpyxl", "lxml", "et_xmlfile", "defusedxml", "typing_extensions"):
    package = distribution(name)
    sections.append(f"{package.metadata['Name']} {package.version}\n")
    sections.append("License: " + str(package.metadata.get("License-Expression") or package.metadata.get("License") or "See included license text") + "\n")
    for file in package.files or []:
        if "license" in str(file).lower() and package.locate_file(file).is_file():
            try:
                sections.append(f"\n--- {file} ---\n" + package.locate_file(file).read_text(encoding="utf-8"))
            except UnicodeError:
                continue
Path(sys.argv[1]).write_text("\n".join(sections), encoding="utf-8")

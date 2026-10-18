"""Keep production modules small; tests and documentation are not production modules."""

from pathlib import Path

root = Path(__file__).resolve().parents[1] / "src" / "apple_remote_sign"
oversized = []
for path in sorted(root.rglob("*.py")):
    lines = len(path.read_text().splitlines())
    if lines > 300:
        oversized.append(f"{path.relative_to(root)}: {lines} lines (maximum 300)")
if oversized:
    raise SystemExit("\n".join(oversized))
print("Production module size check passed (maximum 300 lines).")

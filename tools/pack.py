"""Package the plugin as a zip for upload to the Claude desktop app.

The app's plugin import expects `.claude-plugin/plugin.json` at the ZIP ROOT,
so this archives the CONTENTS of plugins/investment-analyst rather than the
directory itself. Output goes to dist/, which is not version-controlled — the
zip is build output and is always regenerable from source.

    python tools/pack.py
"""
import io
import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "plugins", "investment-analyst")

manifest = json.load(io.open(os.path.join(SRC, ".claude-plugin", "plugin.json"),
                             encoding="utf-8"))
OUT = os.path.join(ROOT, "dist",
                   "investment-analyst-v%s.zip" % manifest["version"])
os.makedirs(os.path.dirname(OUT), exist_ok=True)

print("plugin:", manifest["name"], "v" + manifest["version"])

count = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for name in files:
            if name.endswith(".pyc"):
                continue
            full = os.path.join(root, name)
            z.write(full, os.path.relpath(full, SRC).replace(os.sep, "/"))
            count += 1

with zipfile.ZipFile(OUT) as z:
    bad = z.testzip()

print("files :", count)
print("size  :", round(os.path.getsize(OUT) / 1024, 1), "KB")
print("out   :", os.path.relpath(OUT, ROOT))
print("integrity:", "OK" if bad is None else "CORRUPT: " + bad)

#!/usr/bin/env python3
"""Merge the prediction dashboard into the Comparison dashboard.

Run from your project folder (the one with server.py):
    python comparison/update/apply_update.py

Backups of every file it changes go to comparison/update/backup/.
Nothing is written unless ALL edits can be applied.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATIC = ROOT / "comparison" / "static"
FILES = {
    "js": STATIC / "dashboard.js",
    "html": STATIC / "dashboard.html",
    "css": STATIC / "dashboard.css",
    "server": ROOT / "server.py",
    "service": ROOT / "comparison" / "dashboard_service.py",
}
REPLACED_FUNCTIONS = [
    "createComparisonFields", "loadComparison", "zeroComparisonData",
    "renderComparison", "scheduleComparisonChart",
]
NEW_FUNCTIONS = ["mapOfflineComparisonFilters"]   # added before loadComparison


def read(path):
    return path.read_text(encoding="utf-8")


def find_function(src, name):
    start = re.search(rf"(?m)^(?:async\s+)?function\s+{re.escape(name)}\s*\(", src)
    if not start:
        return None
    end = re.search(r"(?m)^\}[ \t]*$", src[start.end():])
    if not end:
        return None
    return start.start(), start.end() + end.end()


def replace_function(src, name, new_source):
    span = find_function(src, name)
    if span is None:
        raise RuntimeError(f"function {name}() not found")
    return src[:span[0]] + new_source.strip("\n") + src[span[1]:]


def between(src, start_marker, end_marker, new_text):
    start, end = src.find(start_marker), src.find(end_marker)
    if start < 0 or end < 0:
        return None
    return src[:start] + new_text.strip("\n") + src[end + len(end_marker):]


def patch_js(src):
    replacements = read(HERE / "functions_replace.js")
    for name in REPLACED_FUNCTIONS:
        span = find_function(replacements, name)
        if span is None:
            raise RuntimeError(f"{name}() missing from functions_replace.js")
        src = replace_function(src, name, replacements[span[0]:span[1]])
    # helper used only by the offline report
    if find_function(src, "mapOfflineComparisonFilters") is None:
        span = find_function(replacements, "mapOfflineComparisonFilters")
        helper = replacements[span[0]:span[1]]
        anchor = find_function(src, "loadComparison")
        src = src[:anchor[0]] + helper + "\n\n" + src[anchor[0]:]
    # the Numbers/Charts switch no longer exists
    src = re.sub(
        r'\n[ \t]*\$\("#comparison-view-mode"\)\.addEventListener\("change", \(event\) => \{\s*'
        r'setComparisonView\(event\.target\.value\);\s*\}\);',
        "", src,
    )
    block = read(HERE / "forecast_block.js")
    updated = between(src, "// >>> COMPARISON FORECAST START", "// <<< COMPARISON FORECAST END", block)
    if updated is None:
        tail = src.rfind("\ninitialize();")
        if tail < 0:
            raise RuntimeError("final initialize(); call not found in dashboard.js")
        updated = src[:tail] + "\n" + block.strip("\n") + "\n" + src[tail:]
    return updated


def patch_html(src):
    start = src.find('    <section id="comparison-view"')
    toast = src.find('    <div id="toast"')
    if start < 0 or toast < 0 or toast < start:
        raise RuntimeError("comparison section / toast anchors not found in dashboard.html")
    new = src[:start] + read(HERE / "comparison_section.html").rstrip("\n") + "\n    </div>\n" + src[toast:]
    # drop the tags from the earlier stand-alone Prediction tab
    new = re.sub(r'[ \t]*<link rel="stylesheet" href="/assets/prediction\.css[^"]*">\r?\n', "", new)
    new = re.sub(r'[ \t]*<script src="/assets/prediction\.js[^"]*" defer></script>\r?\n', "", new)
    return new


def patch_css(src):
    block = read(HERE / "comparison_forecast.css")
    updated = between(src, "/* >>> comparison forecast css */", "/* <<< comparison forecast css */", block)
    return updated if updated is not None else src.rstrip("\n") + "\n\n" + block.strip("\n") + "\n"


def patch_server(src):
    if "/api/comparison/forecast" in src:
        return src
    if "prediction_service" not in src:
        anchor = "from comparison import backend, dashboard_service, database\n"
        if anchor not in src:
            raise RuntimeError("could not add the comparison forecast import to server.py")
        src = src.replace(
            anchor,
            anchor.rstrip("\n") + ", prediction_service\n",
            1,
        )
    route = (
        '            elif request.path == "/api/comparison/forecast":\n'
        '                parameters = self._query_dict(request.query)\n'
        '                parameters["filters"] = json.loads(parameters.pop("filters", "{}"))\n'
        '                self._json_response(prediction_service.get_comparison_forecast(parameters))\n'
    )
    pattern = r'(?m)^([ \t]*)elif request\.path == "/api/comparison/export\.csv":'
    match = re.search(pattern, src)
    if not match:
        raise RuntimeError('route anchor "/api/comparison/export.csv" not found in server.py')
    return src[:match.start()] + route + src[match.start():]


def patch_service(src):
    if "custom_labels" in src:
        return src
    pattern = (
        r'(    else:\n        labels = \["First period", "Second period"\]\n)'
        r'(    if labels\[0\] == labels\[1\]:)'
    )
    addition = (
        '    custom_labels = filters.get("labels")\n'
        '    if isinstance(custom_labels, list) and len(custom_labels) == 2:\n'
        '        labels = [str(item) for item in custom_labels]\n'
    )
    new, count = re.subn(pattern, lambda m: m.group(1) + addition + m.group(2), src, count=1)
    if not count:
        raise RuntimeError("labels anchor not found in dashboard_service.py")
    return new


def main():
    missing = [str(path) for path in FILES.values() if not path.exists()]
    if missing:
        sys.exit("Could not find: " + ", ".join(missing) + "\nRun this from your project folder.")
    patchers = {"js": patch_js, "html": patch_html, "css": patch_css,
                "server": patch_server, "service": patch_service}
    results, errors = {}, []
    for key, patcher in patchers.items():
        try:
            results[key] = patcher(read(FILES[key]))
        except Exception as error:  # report every problem at once
            errors.append(f"{FILES[key].name}: {error}")
    if errors:
        print("Nothing was changed. Problems found:")
        for line in errors:
            print("  -", line)
        sys.exit(1)

    backup = HERE / "backup"
    backup.mkdir(exist_ok=True)
    for key, path in FILES.items():
        target = backup / path.name
        if not target.exists():
            shutil.copy2(path, target)
        path.write_text(results[key], encoding="utf-8")
        print("updated", path.relative_to(ROOT))
    for old in (STATIC / "prediction.js", STATIC / "prediction.css"):
        if old.exists():
            old.rename(old.with_suffix(old.suffix + ".removed"))
            print("retired", old.relative_to(ROOT), "(renamed to .removed)")
    print("\nDone. Restart the server and hard-refresh the browser (Ctrl+F5).")


if __name__ == "__main__":
    main()
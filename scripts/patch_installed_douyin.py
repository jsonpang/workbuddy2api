"""Apply the source fix to the existing Python 3.10 PyInstaller executable.

Preserves every original archive member except the two growth modules. In
particular, executes the original backend module before adding the new method,
so newer fork code cannot replace unrelated installed behavior.
"""
import argparse
import ast
import hashlib
import json
import marshal
from pathlib import Path
import struct
import sys
import types
import zlib

MAGIC = b"MEI\014\013\012\013\016"
BASE_SHA256 = "110027f31bfbd0bb40f0e997ffd988e18bd21370d7ab4c6742742190f31a543b"


def semantic(code):
    return (code.co_code, code.co_names, code.co_varnames, code.co_freevars,
            code.co_cellvars, tuple(semantic(c) if isinstance(c, types.CodeType) else c
                                   for c in code.co_consts))


def patch(input_path, output_path, baseline_source):
    if sys.version_info[:2] != (3, 10):
        raise RuntimeError("Build with the isolated Python 3.10 runtime")
    blob = input_path.read_bytes()
    original_sha = hashlib.sha256(blob).hexdigest()
    if original_sha != BASE_SHA256:
        raise RuntimeError("Input executable differs from the inspected baseline")
    cookie_at = blob.rfind(MAGIC)
    magic, size, toc_at, toc_size, version, library = struct.unpack("!8sIIII64s", blob[cookie_at:cookie_at + 88])
    assert version == 310 and cookie_at + 88 == len(blob)
    start = cookie_at + 88 - size
    entries, pos = [], start + toc_at
    while pos < start + toc_at + toc_size:
        length, offset, compressed, plain, flag, kind = struct.unpack("!IIIIBc", blob[pos:pos + 18])
        name_bytes = blob[pos + 18:pos + length]
        entries.append([length, offset, compressed, plain, flag, kind, name_bytes])
        pos += length
    pyz_entry = next(e for e in entries if e[6].rstrip(b"\0") == b"PYZ.pyz")
    pyz = blob[start + pyz_entry[1]:start + pyz_entry[1] + pyz_entry[2]]
    modules = marshal.loads(pyz[struct.unpack("!i", pyz[8:12])[0]:])
    root = Path(__file__).resolve().parents[1]
    rebuilt, table, changed = bytearray(pyz[:12]), [], []
    for name, (kind, offset, length) in modules:
        packed = pyz[offset:offset + length]
        if name in ("admin.growth_plans", "admin.backend"):
            original = marshal.loads(zlib.decompress(packed))
            if name == "admin.growth_plans":
                baseline = compile((baseline_source / "admin/growth_plans.py").read_bytes(), original.co_filename, "exec")
                assert semantic(original) == semantic(baseline), "Fork strategy module differs from installed version"
                replacement = compile((root / "admin/growth_plans.py").read_bytes(), original.co_filename, "exec")
            else:
                tree = ast.parse((root / "admin/backend.py").read_text(encoding="utf-8"))
                cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AccountSession")
                method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "fire_douyin_expert")
                wrapper = compile("exec('__ORIGINAL_BACKEND_CODE__')\n" + ast.unparse(method)
                                  + "\nAccountSession.fire_douyin_expert = fire_douyin_expert\ndel fire_douyin_expert\n",
                                  original.co_filename, "exec")
                replacement = wrapper.replace(co_consts=tuple(original if c == "__ORIGINAL_BACKEND_CODE__" else c
                                                               for c in wrapper.co_consts))
            packed = zlib.compress(marshal.dumps(replacement), 9)
            assert isinstance(marshal.loads(zlib.decompress(packed)), types.CodeType)
            changed.append(name)
        table.append((name, (kind, len(rebuilt), len(packed))))
        rebuilt.extend(packed)
    rebuilt[8:12] = struct.pack("!i", len(rebuilt))
    rebuilt.extend(marshal.dumps(table))
    # Independently validate names, order, and every untouched compressed module.
    checked = marshal.loads(rebuilt[struct.unpack("!i", rebuilt[8:12])[0]:])
    assert [m[0] for m in modules] == [m[0] for m in checked]
    for (name, (_, off, length)), (_, (_, newoff, newlen)) in zip(modules, checked):
        if name not in changed:
            assert pyz[off:off + length] == rebuilt[newoff:newoff + newlen]
    data, new_entries = bytearray(), []
    for length, offset, compressed, plain, flag, kind, name_bytes in entries:
        content = blob[start + offset:start + offset + compressed]
        if name_bytes.rstrip(b"\0") == b"PYZ.pyz":
            content = bytes(rebuilt)
            compressed = plain = len(content)
        new_entries.append(struct.pack("!IIIIBc", length, len(data), compressed, plain, flag, kind) + name_bytes)
        data.extend(content)
    toc = b"".join(new_entries)
    cookie = struct.pack("!8sIIII64s", magic, len(data) + len(toc) + 88, len(data), len(toc), version, library)
    output = blob[:start] + bytes(data) + toc + cookie
    output_path.write_bytes(output)
    report = {"original_sha256": original_sha, "patched_sha256": hashlib.sha256(output).hexdigest(),
              "changed_modules": changed, "unchanged_python_modules": len(modules) - len(changed),
              "outer_members": len(entries), "source_snapshot": "youfengknight/workbuddy2api@f2947375cec786596a99abac050195daad2a82a8"}
    output_path.with_suffix(".manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("baseline_source", type=Path)
    args = parser.parse_args()
    patch(args.input, args.output, args.baseline_source)

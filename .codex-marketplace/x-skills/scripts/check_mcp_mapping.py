#!/usr/bin/env python3
"""The documented MCP read path must match the library it replaces.

A skill reading through the Apify connector gets the actor's raw output and has
only `references/mcp-read-layer.md` to turn it into the records every other
skill expects. If that document drifts from the client, the connector path
starts producing a second, subtly different contract: same field names, one of
them silently absent or fed from the wrong place. Nothing at runtime would
notice, because both shapes are plain dicts.

So it is checked rather than trusted. Three things must agree with the code:
the actor ids, every input key the client sends, and every flat field the
normalizer produces.

Run from the repo root:

    python3 scripts/check_mcp_mapping.py
"""
import ast
import importlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOC = ROOT / "references" / "mcp-read-layer.md"
CLIENT = ROOT / "lib" / "apify_client.py"
#: (module, attribute) of the function that flattens an actor row.
NORMALIZER = ("lib.apify_client", "_tweet")
#: Input keys that are near-misses, documented so nobody sends them again.
WARN_ABOUT = ("sort",)


def mentions(doc: str, name: str, extra: str = "") -> bool:
    """A name counts as documented only as a whole word.

    `name not in doc` is satisfied by any longer name containing it, so
    queryTypeX would silently pass for queryType. Actor ids need `extra` to
    cover the characters they are allowed to contain, or a rename to
    actor-name-v2 passes as actor-name.
    """
    char = "A-Za-z0-9_" + extra
    return re.search(rf"(?<![{char}]){re.escape(name)}(?![{char}])", doc) is not None


def actor_ids(tree: ast.AST) -> set[str]:
    """Every string constant in the client that looks like an actor id."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.fullmatch(r"[A-Za-z0-9_.-]+~[A-Za-z0-9_.-]+", node.value):
                found.add(node.value.replace("~", "/"))
    return found


def payload_keys(tree: ast.AST) -> set[str]:
    """Keys of every literal dict passed to the actor-running helper."""
    keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name not in ("_run", "_run_sync"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                keys |= {k.value for k in arg.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


def flat_fields() -> set[str]:
    module = importlib.import_module(NORMALIZER[0])
    produced = getattr(module, NORMALIZER[1])({})
    return {k for k in produced if not k.startswith("_")}


def main() -> int:
    problems = []

    if not DOC.is_file():
        print(f"{DOC.relative_to(ROOT)} is missing; the connector path is undocumented.")
        return 1
    doc = DOC.read_text(encoding="utf-8")
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))

    for actor in sorted(actor_ids(tree)):
        if not mentions(doc, actor, extra=r"./\-"):
            problems.append(f"actor {actor} is called by the client and absent from the doc")

    keys = payload_keys(tree)
    if not keys:
        problems.append("no literal actor payload found in the client: has it changed shape?")
    for key in sorted(keys):
        if not mentions(doc, key):
            problems.append(f"input key `{key}` is sent by the client and undocumented")

    for key in sorted(WARN_ABOUT):
        if not mentions(doc, key):
            problems.append(f"the doc does not warn about `{key}`, which is ignored and bills a run")

    fields = flat_fields()
    if len(fields) < 5:
        problems.append("the normalizer produced almost nothing: wrong attribute?")
    for field in sorted(fields):
        if not re.search(r"`" + re.escape(field) + r"`", doc):
            problems.append(f"flat field `{field}` is produced by the client and undocumented")

    table = set(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \|", doc, re.M))
    for field in sorted(table - fields):
        problems.append(f"the mapping table documents `{field}`, which the client never produces")

    if "mcp.apify.com?actors=" not in doc:
        problems.append("the doc does not give the filtered connector URL")
    if "call-actor" not in doc:
        problems.append("the doc does not explain why the bare server is not the one to use")

    if problems:
        print("MCP read-layer mapping:\n" + "\n".join("  " + p for p in problems))
        return 1

    print(f"OK: {len(actor_ids(tree))} actor(s), {len(keys)} input key(s) and "
          f"{len(fields)} flat field(s) all documented in references/mcp-read-layer.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

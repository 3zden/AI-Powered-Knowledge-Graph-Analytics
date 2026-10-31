"""Phase 2b - entity resolution: map surface forms ("Dr. Amara Okafor", "A. Okafor", "Helios Robotics SA")
to one canonical entity. Rule-based clustering (union-find) over cleaned names:
  1. same normalized key            "Helios Robotics SA" == "Helios Robotics"
  2. unique token containment       "Helios" -> "Helios Robotics" (only if exactly one candidate)
  3. person initials                "A. Okafor" -> "Amara Okafor"
  4. near-identical spelling        difflib ratio >= 0.92

hdfs:/extracted/raw.jsonl -> hdfs:/extracted/resolved.jsonl  (+ unresolved.jsonl for the ablation study)
"""
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from common import hdfs_jsonl, hdfs_put, hdfs_put_jsonl

HONORIFICS = r"^(dr|mr|mrs|ms|prof|sir)\.?\s+"
LEGAL = r"[,\s]+(inc|ltd|llc|llp|sa|s\.p\.a|spa|as|ag|gmbh|corp|plc|bv|sas)\.?$"


def clean(name):
    """Display form: drop honorifics, legal suffixes and a leading 'the'."""
    name = re.sub(r"^the\s+", "", name.strip(), flags=re.I)
    name = re.sub(HONORIFICS, "", name, flags=re.I)
    while re.search(LEGAL, name, flags=re.I):
        name = re.sub(LEGAL, "", name, flags=re.I)
    return name.strip()


def key(name):
    return " ".join(re.sub(r"[^\w\s]", " ", clean(name).casefold()).split())


def _initials_match(short, long):
    s, l = short.split(), long.split()
    return (len(s) == len(l) >= 2 and len(s[0]) == 1 and l[0].startswith(s[0]) and s[1:] == l[1:])


def resolve_names(mentions):
    """mentions: list of (surface name, type). Returns {surface name: canonical name}."""
    types = defaultdict(Counter)
    counts = Counter()
    for name, etype in mentions:
        types[key(name)][etype] += 1
        counts[name] += 1
    keys = sorted(k for k in types if k)
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def typ(k):
        return types[k].most_common(1)[0][0]

    for k in keys:
        others = [o for o in keys if o != k and (typ(o) == typ(k) or None in (typ(o), typ(k)))]
        contained = [o for o in others if len(o) > len(k) and set(k.split()) < set(o.split())]
        # ponytail: O(n^2) pairwise scan, fine for thousands of names; block by first token if it grows
        if len(contained) == 1:
            parent[find(k)] = find(contained[0])
        for o in others:
            if _initials_match(k, o) or SequenceMatcher(None, k, o).ratio() >= 0.92:
                parent[find(k)] = find(o)

    clusters = defaultdict(Counter)
    for name, n in counts.items():
        if key(name):
            clusters[find(key(name))][clean(name)] += n
    canonical = {root: max(c, key=lambda s: (c[s], len(s))) for root, c in clusters.items()}
    return {name: canonical[find(key(name))] for name in counts if key(name)}


def annotate(rows, mapping):
    """Rewrite names via mapping and attach endpoint types to relations (used by Spark validation)."""
    global_type = defaultdict(Counter)
    for r in rows:
        for e in r["entities"]:
            global_type[str(e.get("name"))][e.get("type")] += 1
    out = []
    for r in rows:
        local = {str(e.get("name")): e.get("type") for e in r["entities"]}

        def etype(n):
            return local.get(n) or (global_type[n].most_common(1)[0][0] if global_type[n] else None)

        ents = [{"name": mapping.get(str(e.get("name")), str(e.get("name"))), "surface": e.get("name"),
                 "type": e.get("type"), "description": e.get("description")} for e in r["entities"]]
        rels = [{"source": mapping.get(str(x.get("source")), str(x.get("source"))),
                 "target": mapping.get(str(x.get("target")), str(x.get("target"))),
                 "type": str(x.get("type", "")), "source_type": etype(str(x.get("source"))),
                 "target_type": etype(str(x.get("target"))), "evidence": x.get("evidence"),
                 "confidence": x.get("confidence")} for x in r["relations"]]
        out.append({"doc_id": r["doc_id"], "entities": ents, "relations": rels})
    return out


def main():
    rows = hdfs_jsonl("/extracted/raw.jsonl")
    types = {}
    for r in rows:
        for e in r["entities"]:
            types[str(e.get("name"))] = e.get("type")
    mentions = [(str(e.get("name")), e.get("type")) for r in rows for e in r["entities"]]
    mentions += [(str(x.get(end)), types.get(str(x.get(end)))) for r in rows for x in r["relations"]
                 for end in ("source", "target")]
    mapping = resolve_names(mentions)
    hdfs_put_jsonl("/extracted/resolved.jsonl", annotate(rows, mapping))
    hdfs_put_jsonl("/extracted/unresolved.jsonl", annotate(rows, {}))
    hdfs_put("/extracted/aliases.tsv", "".join(f"{k}\t{v}\n" for k, v in sorted(mapping.items())))
    merged = {k: v for k, v in mapping.items() if k != v}
    for k, v in sorted(merged.items()):
        print(f"  {k!r:<40} -> {v!r}")
    print(f"resolve: {len(mapping)} surface names -> {len(set(mapping.values()))} entities")


if __name__ == "__main__":
    main()

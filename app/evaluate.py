"""Phase 6 - benchmark against the hand-annotated ground truth (data/gold/gold.json).

Answers the research questions of the project brief:
  RQ1 extraction accuracy (entity / relation P-R-F1, before and after Spark schema validation)
  RQ2 effect of entity resolution (duplicates, multi-hop path support, downstream QA)
  RQ3 Cypher vs GraphRAG per question type          RQ4 latency / token cost
  RQ5 provenance: citation precision of GraphRAG answers
Writes /reports/benchmark.{json,md}.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from common import SYMMETRIC, hdfs_jsonl, run_cypher, validate_relation
from graphrag import ask
from resolve import key

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
REPORTS = Path(os.environ.get("REPORTS_DIR", "/reports"))
GOLD = json.loads((DATA_DIR / "gold" / "gold.json").read_text())
ALIAS = {key(n): e["name"] for e in GOLD["entities"] for n in [e["name"], *e["aliases"]]}


def to_gold(name):
    return ALIAS.get(key(str(name)))


def norm_triple(s, t, d):
    if t in SYMMETRIC:
        s, d = sorted((s, d), key=str)
    return (s, t, d)


def prf(pred, gold):
    tp = len(pred & gold)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gold) if gold else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
            "tp": tp, "predicted": len(pred), "gold": len(gold)}


# --- RQ1: extraction ----------------------------------------------------------
def extraction_quality(raw_rows, unresolved_rows):
    gold_ents = {e["name"] for e in GOLD["entities"]}
    gold_rels = {norm_triple(*r) for r in GOLD["relations"]}
    pred_ents = {to_gold(e.get("name")) or f"?{key(str(e.get('name')))}" for r in raw_rows for e in r["entities"]}
    llm_rels = {norm_triple(to_gold(x.get("source")), str(x.get("type", "")).upper(), to_gold(x.get("target")))
                for r in raw_rows for x in r["relations"]}
    validated = {norm_triple(to_gold(v["source"]), v["type"], to_gold(v["target"]))
                 for r in unresolved_rows for x in r["relations"] if (v := validate_relation(x))}
    return {"entities": prf(pred_ents, gold_ents),
            "relations_llm_raw": prf(llm_rels, gold_rels),
            "relations_after_validation": prf(validated, gold_rels)}


# --- RQ2: entity resolution ------------------------------------------------
def validated_edges(rows):
    return {(v["source"], v["type"], v["target"]) for r in rows for x in r["relations"] if (v := validate_relation(x))}


def path_supported(path, edges):
    """True if the gold path exists in the graph with consistent node identity (same node name for a
    shared gold entity) - i.e. a multi-hop traversal could actually walk it."""
    def search(i, binding):
        if i == len(path):
            return True
        gs, gt, gd = path[i]
        for s, t, d in edges:
            if t != gt:
                continue
            for a, b in ((s, d), (d, s)) if t in SYMMETRIC else ((s, d),):
                if to_gold(a) == gs and to_gold(b) == gd and binding.get(gs, a) == a and binding.get(gd, b) == b:
                    if search(i + 1, {**binding, gs: a, gd: b}):
                        return True
        return False
    return search(0, {})


def resolution_effect(rows):
    edges = validated_edges(rows)
    names = {n for e in edges for n in (e[0], e[2])} | {e["name"] for r in rows for e in r["entities"]}
    matched = {n for n in names if to_gold(n)}
    gold_hit = {to_gold(n) for n in matched}
    paths = [q["path"] for q in GOLD["questions"] if "path" in q]
    return {"nodes": len(names), "nodes_matching_gold": len(matched), "gold_entities_found": len(gold_hit),
            "duplicate_nodes": len(matched) - len(gold_hit),
            "fragmentation": round(len(matched) / max(len(gold_hit), 1), 3),
            "multi_hop_paths_supported": f"{sum(path_supported(p, edges) for p in paths)}/{len(paths)}"}


# --- RQ3-5: question answering ---------------------------------------------
def answer_recall(text, groups):
    t = text.casefold()
    hits = [any(re.search(rf"\b{re.escape(a.casefold())}\b", t) for a in g) for g in groups]
    return sum(hits) / len(groups)


def run_qa(label):
    results = []
    for q in GOLD["questions"]:
        t0 = time.perf_counter()
        try:
            rows = run_cypher(q["cypher"])
            error = None
        except Exception as e:  # a wrong graph shape must not abort the benchmark
            rows, error = [], str(e)
        cy_s = time.perf_counter() - t0
        values = [str(v) for row in rows for v in row.values() if v is not None]
        cy_prec = (sum(answer_recall(v, q["answer"]) > 0 for v in values) / len(values)) if values else 0.0

        rag = ask(q["question"])
        cited = rag["citations"]
        good = [c for c in cited if any(s in c for s in q["sources"])]
        results.append({
            "graph": label, "id": q["id"], "type": q["type"], "question": q["question"],
            "cypher": {"result": values, "error": error, "recall": answer_recall(" | ".join(values), q["answer"]),
                       "precision": round(cy_prec, 3), "latency_s": round(cy_s, 3)},
            "graphrag": {"answer": rag["answer"], "recall": answer_recall(rag["answer"], q["answer"]),
                         "latency_s": rag["latency_s"], "retrieval_s": rag["retrieval_s"],
                         "tokens": rag["prompt_tokens"] + rag["completion_tokens"],
                         "citations": cited, "invalid_citations": rag["invalid_citations"],
                         "citation_precision": round(len(good) / len(cited), 3) if cited else None,
                         "facts_in_context": len(rag["context"]["facts"])},
        })
        print(f"  [{label}] {q['id']:<4} cypher={results[-1]['cypher']['recall']:.2f} "
              f"graphrag={results[-1]['graphrag']['recall']:.2f} ({rag['latency_s']}s)")
    return results


def summarize(results, approach, label, qtype=None):
    rs = [r[approach] for r in results if r["graph"] == label and (qtype is None or r["type"] == qtype)]
    out = {"n": len(rs), "accuracy": round(sum(r["recall"] == 1 for r in rs) / len(rs), 3),
           "mean_recall": round(sum(r["recall"] for r in rs) / len(rs), 3),
           "mean_latency_s": round(sum(r["latency_s"] for r in rs) / len(rs), 3)}
    if approach == "graphrag":
        cps = [r["citation_precision"] for r in rs if r["citation_precision"] is not None]
        out |= {"mean_tokens": round(sum(r["tokens"] for r in rs) / len(rs)),
                "answers_with_citations": f"{len(cps)}/{len(rs)}",
                "citation_precision": round(sum(cps) / len(cps), 3) if cps else None}
    return out


def load_graph(variant):
    subprocess.run(["spark-submit", "--master", "local[*]", "spark_etl.py", variant], check=True,
                   cwd=Path(__file__).parent)


def markdown(report):
    def table(rows, cols):
        head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
        return head + "".join("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n" for r in rows)

    ex, cost = report["extraction"], report["cost"]
    md = ["# Benchmark report: Graph Analytics (Cypher) vs GraphRAG\n",
          f"Model: `{cost['model']}` - documents: {cost['documents']} - generated {report['generated_at']}\n",
          "## RQ1 - LLM extraction accuracy (lenient alias matching against gold)\n",
          table([{"level": k, **v} for k, v in ex.items()], ["level", "precision", "recall", "f1", "tp", "predicted", "gold"]),
          "\n`relations_after_validation` = after the Spark schema validation step (type normalization, direction repair).\n",
          "## RQ2 - Effect of entity resolution\n",
          table([{"graph": k, **v} for k, v in report["resolution"].items()],
                ["graph", "nodes", "nodes_matching_gold", "gold_entities_found", "duplicate_nodes", "fragmentation",
                 "multi_hop_paths_supported"]),
          "\n## RQ3 - Cypher vs GraphRAG by question type\n",
          "Scoring is mention-based: an answer gets credit for each gold item it names. This is lenient for "
          "free-text GraphRAG answers (an answer that mentions the gold entity while picking another one still "
          "scores), so check the per-question table below.\n\n"]
    rows = []
    for label in report["graphs"]:
        for qtype in ["all", *sorted({q["type"] for q in GOLD["questions"]})]:
            for approach in ("cypher", "graphrag"):
                s = summarize(report["qa"], approach, label, None if qtype == "all" else qtype)
                rows.append({"graph": label, "type": qtype, "approach": approach, **s})
    md.append(table(rows, ["graph", "type", "approach", "n", "accuracy", "mean_recall", "mean_latency_s",
                           "mean_tokens", "citation_precision"]))
    md += ["\n## RQ4 - Cost and latency\n",
           f"- Extraction: {cost['extraction_tokens']} tokens, {cost['extraction_s']} s LLM time "
           f"({cost['extraction_s_per_doc']} s/doc).\n",
           f"- Cypher queries: 0 tokens at query time; GraphRAG: see `mean_tokens` above.\n",
           "\n## RQ5 - Provenance\n",
           "Every node and edge stores the ids of the documents it was extracted from (`sources`) and the "
           "supporting quotes (`evidence`). GraphRAG answers cite these ids; `citation_precision` is the share of "
           "cited sources that are gold supporting documents for the question. Invalid citations "
           f"(ids not present in the retrieved context): {report['invalid_citations']}.\n",
           "\n## Per-question results (resolved graph)\n",
           table([{"id": r["id"], "type": r["type"], "cypher": r["cypher"]["recall"], "graphrag": r["graphrag"]["recall"],
                   "rag_answer": r["graphrag"]["answer"].replace("\n", " ").replace("|", "/")[:160]}
                  for r in report["qa"] if r["graph"] == "resolved"], ["id", "type", "cypher", "graphrag", "rag_answer"])]
    return "".join(md)


def main():
    raw = hdfs_jsonl("/extracted/raw.jsonl")
    unresolved = hdfs_jsonl("/extracted/unresolved.jsonl")
    resolved = hdfs_jsonl("/extracted/resolved.jsonl")
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "extraction": extraction_quality(raw, unresolved),
        "resolution": {"unresolved": resolution_effect(unresolved), "resolved": resolution_effect(resolved)},
        "cost": {"model": raw[0]["model"] if raw else None, "documents": len(raw),
                 "extraction_tokens": sum(r["prompt_tokens"] + r["completion_tokens"] for r in raw),
                 "extraction_s": round(sum(r["latency_s"] for r in raw), 1),
                 "extraction_s_per_doc": round(sum(r["latency_s"] for r in raw) / max(len(raw), 1), 1)},
        "graphs": ["unresolved", "resolved"], "qa": [],
    }
    for variant in report["graphs"]:  # ends with the resolved graph loaded
        load_graph(variant)
        report["qa"] += run_qa(variant)
    report["summary"] = {f"{g}/{a}": summarize(report["qa"], a, g) for g in report["graphs"] for a in ("cypher", "graphrag")}
    report["invalid_citations"] = sum(len(r["graphrag"]["invalid_citations"]) for r in report["qa"])
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "benchmark.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    (REPORTS / "benchmark.md").write_text(markdown(report))
    print(json.dumps(report["summary"], indent=1))
    print(f"evaluate: report written to {REPORTS}/benchmark.md")


if __name__ == "__main__":
    main()

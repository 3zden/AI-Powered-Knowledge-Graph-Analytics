"""Phase 5 - GraphRAG: fulltext seed retrieval -> 2-hop subgraph expansion -> LLM answer with citations."""
import re
import sys
import time

from common import chat, run_cypher

STOP = set("""who what which where when why how many much is are was were be the a an of in on at to for and or
with does did do has have had that this its it their there one two different any all""".split())

SYSTEM = """You answer questions using ONLY the knowledge-graph facts provided.
The facts were automatically extracted from source documents and may be incomplete or wrong:
present them as claims from the sources, not verified truth.
After each claim, cite the supporting source ids in square brackets, e.g. [pdf:report.pdf].
Entity descriptions count as facts too.
If the facts do not contain the answer, say "The graph does not contain enough information."
Answer in at most three sentences."""


def seed_query(question):
    terms = [w for w in re.findall(r"[A-Za-z0-9]+", question) if w.lower() not in STOP and len(w) > 2]
    return " OR ".join(f"{t}~" if len(t) > 4 else t for t in terms)  # fuzzy match for longer words


def retrieve(question, seeds=5, max_facts=60):
    q = seed_query(question)
    seed_rows = run_cypher("CALL db.index.fulltext.queryNodes('entity_text', $q) YIELD node, score "
                           "RETURN node.name AS name, score LIMIT $k", q=q, k=seeds) if q else []
    # keep only strong seeds: a small model drowns in a whole-graph context
    names = [r["name"] for r in seed_rows if r["score"] >= 0.5 * seed_rows[0]["score"]]
    facts = run_cypher(
        "UNWIND $names AS n MATCH (s:Entity {name: n}) "
        "MATCH p = (s)-[*1..2]-(:Entity) WHERE all(r IN relationships(p) WHERE type(r) <> 'MENTIONED_IN') "
        "UNWIND relationships(p) AS r WITH r, min(length(p)) AS hops "  # 1-hop facts first
        "RETURN startNode(r).name AS source, type(r) AS type, endNode(r).name AS target, "
        "r.sources AS sources, r.evidence AS evidence, r.confidence AS confidence ORDER BY hops LIMIT $max",
        names=names, max=max_facts)
    involved = set(names) | {f["source"] for f in facts} | {f["target"] for f in facts}
    entities = run_cypher("MATCH (e:Entity) WHERE e.name IN $names "
                          "RETURN e.name AS name, e.type AS type, e.description AS description, e.sources AS sources",
                          names=sorted(involved))
    return {"seeds": names, "entities": entities, "facts": facts}


def format_context(ctx):
    lines = ["Entities:"]
    lines += [f"- {e['name']} ({e['type']}): {e['description'] or ''} [{', '.join(e['sources'] or [])}]"
              for e in ctx["entities"]]
    lines.append("Facts:")
    lines += [f"- {f['source']} {f['type']} {f['target']} (confidence {f['confidence']}) [{', '.join(f['sources'] or [])}]"
              for f in ctx["facts"]]
    return "\n".join(lines)


def ask(question):
    t0 = time.perf_counter()
    ctx = retrieve(question)
    retrieval_s = time.perf_counter() - t0
    answer, usage, llm_s = chat([{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": f"{format_context(ctx)}\n\nQuestion: {question}"}])
    known = {s for item in ctx["entities"] + ctx["facts"] for s in (item["sources"] or [])}
    cited = {c.strip() for group in re.findall(r"\[([^\]]+)\]", answer) for c in re.split(r"[,;]", group)}
    return {
        "question": question, "answer": answer,
        "citations": sorted(cited & known), "invalid_citations": sorted(cited - known),
        "context": ctx, "retrieval_s": round(retrieval_s, 3), "llm_s": round(llm_s, 3),
        "latency_s": round(time.perf_counter() - t0, 3), **usage,
    }


if __name__ == "__main__":
    out = ask(" ".join(sys.argv[1:]) or "Who founded Helios Robotics?")
    print(out["answer"], "\ncitations:", out["citations"], f"\n{out['latency_s']}s")

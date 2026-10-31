"""Phase 2a - LLM entity & relation extraction, one call per document (docs are small).

hdfs:/parsed/docs.jsonl -> hdfs:/extracted/raw.jsonl
"""
import json

from common import ENTITY_TYPES, LLM_MODEL, RELATION_TYPES, chat, hdfs_jsonl, hdfs_put_jsonl

PROMPT = """Extract a knowledge graph from the document below.

Entity types: {entity_types}
Relation types (source -> target):
  FOUNDED (Person -> Organization), LEADS (Person -> Organization; CEO, CTO, head),
  WORKS_FOR (Person -> Organization), BOARD_MEMBER_OF (Person -> Organization),
  INVESTED_IN (Organization -> Organization), ACQUIRED (Organization -> Organization),
  PARTNERS_WITH (Organization -> Organization), SUPPLIES (Organization -> Organization),
  DEVELOPS (Organization -> Product), LOCATED_IN (Organization -> Location, city only)

Rules:
- Only extract facts stated in the document. Never guess.
- Use the most complete name written in the document (e.g. "Dr. Jane Roe" -> "Jane Roe"), never pronouns.
- "description" is one short sentence about what the entity is or does, based on the document.
- "evidence" is a short quote from the document supporting the relation.
- "confidence" is a number between 0 and 1.
- Every city where an organization is based is a Location entity with a LOCATED_IN relation.
- Respect the direction: "source" must have the source type, "target" the target type.

Example. Text: "Acme Corp, based in Paris, was founded by Dr. John Doe. Its CEO Jane Roe launched RoboX."
Output:
{{"entities": [{{"name": "Acme Corp", "type": "Organization", "description": "Company based in Paris that makes RoboX."}},
  {{"name": "John Doe", "type": "Person", "description": "Founder of Acme Corp."}},
  {{"name": "Jane Roe", "type": "Person", "description": "CEO of Acme Corp."}},
  {{"name": "RoboX", "type": "Product", "description": "Product launched by Acme Corp."}},
  {{"name": "Paris", "type": "Location", "description": "City where Acme Corp is based."}}],
 "relations": [{{"source": "John Doe", "type": "FOUNDED", "target": "Acme Corp", "evidence": "founded by Dr. John Doe", "confidence": 0.95}},
  {{"source": "Jane Roe", "type": "LEADS", "target": "Acme Corp", "evidence": "Its CEO Jane Roe", "confidence": 0.9}},
  {{"source": "Acme Corp", "type": "DEVELOPS", "target": "RoboX", "evidence": "launched RoboX", "confidence": 0.8}},
  {{"source": "Acme Corp", "type": "LOCATED_IN", "target": "Paris", "evidence": "based in Paris", "confidence": 0.95}}]}}

Return JSON only, with the same shape as the example.

Document ({doc_id}, title: {title}):
\"\"\"
{text}
\"\"\""""


def extract(doc):
    prompt = PROMPT.format(entity_types=", ".join(sorted(ENTITY_TYPES)), doc_id=doc["id"],
                           title=doc["title"], text=doc["text"][:6000])
    text, usage, latency = chat([{"role": "user", "content": prompt}], json_mode=True)
    try:
        out = json.loads(text)
        error = None
    except json.JSONDecodeError as e:
        out, error = {}, str(e)
    return {
        "doc_id": doc["id"], "model": LLM_MODEL, "error": error, "latency_s": round(latency, 2), **usage,
        "entities": [e for e in out.get("entities", []) if isinstance(e, dict)],
        "relations": [r for r in out.get("relations", []) if isinstance(r, dict)],
    }


def main():
    docs = hdfs_jsonl("/parsed/docs.jsonl")
    rows = []
    for doc in docs:
        row = extract(doc)
        rows.append(row)
        print(f"  {doc['id']:<35} {len(row['entities']):>2} entities {len(row['relations']):>2} relations "
              f"{row['latency_s']:>6}s {row['error'] or ''}")
    hdfs_put_jsonl("/extracted/raw.jsonl", rows)
    print(f"extract: {len(rows)} docs, relation types allowed: {len(RELATION_TYPES)} -> hdfs:/extracted/raw.jsonl")


if __name__ == "__main__":
    main()

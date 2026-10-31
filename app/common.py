"""Shared plumbing: graph schema, WebHDFS, LLM (OpenAI-compatible) and Neo4j clients."""
import json
import os
import time

import requests
from neo4j import GraphDatabase

ENTITY_TYPES = {"Person", "Organization", "Product", "Location"}
P, O = "Person", "Organization"
SIGNATURES = {  # relation type -> (source type, target type)
    "FOUNDED": (P, O), "LEADS": (P, O), "WORKS_FOR": (P, O), "BOARD_MEMBER_OF": (P, O),
    "INVESTED_IN": (O, O), "ACQUIRED": (O, O), "PARTNERS_WITH": (O, O), "SUPPLIES": (O, O),
    "DEVELOPS": (O, "Product"), "LOCATED_IN": (O, "Location"),
}
RELATION_TYPES = set(SIGNATURES)
SYNONYMS = {"CEO": "LEADS", "CEO_OF": "LEADS", "CTO": "LEADS", "CTO_OF": "LEADS", "HEADS": "LEADS",
            "FOUNDER_OF": "FOUNDED", "PARTNER_OF": "PARTNERS_WITH", "PARTNERED_WITH": "PARTNERS_WITH",
            "HEADQUARTERED_IN": "LOCATED_IN", "BASED_IN": "LOCATED_IN", "INVESTS_IN": "INVESTED_IN"}
SYMMETRIC = {"PARTNERS_WITH"}


def validate_relation(rel):
    """Schema validation for one extracted relation. Normalizes the type, repairs reversed direction
    using the type signature, and returns None when the relation can't fit the schema."""
    t = str(rel.get("type") or "").upper().replace(" ", "_").replace("-", "_")
    t = SYNONYMS.get(t, t)
    src, dst, st, dt = rel.get("source"), rel.get("target"), rel.get("source_type"), rel.get("target_type")
    if t not in SIGNATURES or not src or not dst or src == dst:
        return None
    want_s, want_t = SIGNATURES[t]

    def fits(a, b):  # unknown endpoint types (None) are accepted
        return a in (None, want_s) and b in (None, want_t)

    if not fits(st, dt):
        if not fits(dt, st):
            return None
        src, dst = dst, src
    if t in SYMMETRIC:
        src, dst = sorted((src, dst))
    try:
        conf = min(max(float(rel.get("confidence") or 0.5), 0.0), 1.0)
    except (TypeError, ValueError):
        conf = 0.5
    return {"source": src, "type": t, "target": dst, "source_type": want_s, "target_type": want_t,
            "evidence": str(rel.get("evidence") or ""), "confidence": conf}

HDFS_URL = os.environ.get("HDFS_URL", "http://localhost:9870")
HDFS_FS = os.environ.get("HDFS_FS", "hdfs://localhost:8020")


# --- HDFS (WebHDFS REST) ---------------------------------------------------
def _webhdfs(method, path, op, **kw):
    params = {"op": op, "user.name": "hadoop", **kw.pop("params", {})}
    r = requests.request(method, f"{HDFS_URL}/webhdfs/v1{path}", params=params, timeout=60, **kw)
    r.raise_for_status()
    return r


def hdfs_put(path, data):
    if isinstance(data, str):
        data = data.encode()
    _webhdfs("PUT", path, "CREATE", params={"overwrite": "true"}, data=data)


def hdfs_get(path):
    return _webhdfs("GET", path, "OPEN").content


def hdfs_ls(path):
    statuses = _webhdfs("GET", path, "LISTSTATUS").json()["FileStatuses"]["FileStatus"]
    return [s["pathSuffix"] for s in statuses]


def hdfs_jsonl(path):
    return [json.loads(line) for line in hdfs_get(path).decode().splitlines() if line.strip()]


def hdfs_put_jsonl(path, rows):
    hdfs_put(path, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


# --- LLM -------------------------------------------------------------------
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:3b")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "ollama")


def chat(messages, json_mode=False):
    """Returns (text, usage dict with prompt/completion tokens, latency seconds)."""
    body = {"model": LLM_MODEL, "messages": messages, "temperature": 0}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    t0 = time.perf_counter()
    r = requests.post(f"{LLM_BASE_URL}/chat/completions", json=body, timeout=900,
                      headers={"Authorization": f"Bearer {LLM_API_KEY}"})
    r.raise_for_status()
    out = r.json()
    usage = out.get("usage") or {}
    return (out["choices"][0]["message"]["content"],
            {"prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0)},
            time.perf_counter() - t0)


# --- Neo4j -----------------------------------------------------------------
def neo4j_driver():
    return GraphDatabase.driver(os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                                auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "graphpass123")))


def run_cypher(query, **params):
    with neo4j_driver() as d:
        records, _, _ = d.execute_query(query, params)
        return [r.data() for r in records]

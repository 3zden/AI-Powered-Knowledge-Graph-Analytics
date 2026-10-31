"""Phase 7 - integrated application: REST API, web UI, API-key access control, metrics, retention."""
import logging
import os
import time
from collections import Counter
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

import analytics
from common import LLM_MODEL, hdfs_ls, run_cypher
from graphrag import ask

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kg-api")
app = FastAPI(title="Knowledge Graph Analytics Platform")

# API_KEYS="key1:reader,key2:admin"
KEYS = dict(item.split(":", 1) for item in os.environ.get("API_KEYS", "").split(",") if ":" in item)
LEVEL = {"reader": 1, "admin": 2}
METRICS = Counter()


def role(min_role):
    def check(request: Request, x_api_key: str = Header(default="")):
        r = KEYS.get(x_api_key)
        if not r or LEVEL.get(r, 0) < LEVEL[min_role]:
            log.warning("denied %s %s", request.method, request.url.path)
            raise HTTPException(401 if not r else 403, "invalid or insufficient API key")
        log.info("audit role=%s %s %s", r, request.method, request.url.path)
        return r
    return check


@app.middleware("http")
async def metrics(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = route.path if route else "unmatched"
    METRICS[f'http_requests_total{{path="{path}",status="{response.status_code}"}}'] += 1
    METRICS[f'http_request_seconds_sum{{path="{path}"}}'] += time.perf_counter() - t0
    return response


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@app.get("/")
def ui():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health")
def health():
    status = {"model": LLM_MODEL}
    for name, probe in {"neo4j": lambda: run_cypher("RETURN 1 AS ok"), "hdfs": lambda: hdfs_ls("/")}.items():
        try:
            probe()
            status[name] = "up"
        except Exception as e:
            status[name] = f"down: {type(e).__name__}"
    return status


@app.post("/ask")
def ask_question(q: Question, _=Depends(role("reader"))):
    out = ask(q.question)
    METRICS["llm_tokens_total"] += out["prompt_tokens"] + out["completion_tokens"]
    METRICS["graphrag_seconds_sum"] += out["latency_s"]
    METRICS["graphrag_questions_total"] += 1
    return out


@app.get("/analytics/{name}")
def run_analysis(name: str, _=Depends(role("reader"))):
    if name not in analytics.ANALYSES:
        raise HTTPException(404, f"unknown analysis, choose from {sorted(analytics.ANALYSES)}")
    return analytics.ANALYSES[name]()


@app.get("/path")
def shortest_path(a: str = Query(max_length=100), b: str = Query(max_length=100), _=Depends(role("reader"))):
    return analytics.path(a, b)


@app.get("/entity")
def entity(name: str = Query(max_length=100), _=Depends(role("reader"))):
    return analytics.entity(name)


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus(_=Depends(role("admin"))):
    return "".join(f"kg_{k} {v}\n" for k, v in sorted(METRICS.items()))


@app.post("/admin/retention")
def retention(days: int = int(os.environ.get("RETENTION_DAYS", 365)), _=Depends(role("admin"))):
    """Delete documents collected more than `days` ago, then the facts no longer backed by any source."""
    # ponytail: graph only; raw files in HDFS /raw keep their own lifecycle (hdfs dfs -rm or HDFS storage policies)
    old = run_cypher("MATCH (d:Document) WHERE datetime(d.fetched_at) < datetime() - duration({days: $days}) "
                     "WITH d, d.id AS id DETACH DELETE d RETURN collect(id) AS ids", days=days)[0]["ids"]
    run_cypher("MATCH (:Entity)-[r]->(:Entity) SET r.sources = [s IN r.sources WHERE NOT s IN $old] "
               "WITH r WHERE size(r.sources) = 0 DELETE r", old=old)
    gone = run_cypher("MATCH (e:Entity) SET e.sources = [s IN e.sources WHERE NOT s IN $old] "
                      "WITH e WHERE size(e.sources) = 0 DETACH DELETE e RETURN count(*) AS n", old=old)[0]["n"]
    log.info("retention: removed %d documents, %d entities", len(old), gone)
    return {"documents_removed": old, "entities_removed": gone}

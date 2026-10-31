# AI-powered knowledge graph analytics platform

Proof of concept of the architecture in *projet_bigdata_analytic*: heterogeneous sources → crawler → HDFS →
LLM extraction + entity resolution → Spark ETL → Neo4j, then **one graph, two analytical approaches**
(Cypher/GDS graph analytics vs GraphRAG) and a benchmark comparing them.

```
websites / REST API / PDF / Word / email / text / SQLite
        │  crawler.py        collection, parsing, metadata
        ▼
      HDFS  /raw/<kind>/*  /parsed/docs.jsonl
        │  extract.py        LLM entity + relation extraction (JSON, with evidence quotes)
        │  resolve.py        entity resolution (alias clustering)
        ▼
      HDFS  /extracted/{raw,resolved,unresolved}.jsonl
        │  spark_etl.py      validation (schema, direction repair), dedup, transform → /curated parquet
        ▼
      Neo4j  (:Entity:Person|Organization|Product|Location)-[REL {sources, evidence, confidence}]->()
             (:Entity)-[:MENTIONED_IN]->(:Document)
        ├── analytics.py     Cypher + GDS (PageRank, betweenness, Louvain, shortest paths)
        └── graphrag.py      fulltext seeds → 2-hop subgraph → LLM answer with source citations
                 ▼
        evaluate.py → reports/benchmark.md      api.py → REST API + web UI (:8080)
```

| Phase | Deliverable | Code |
|---|---|---|
| 1 Sample documents, crawler, HDFS | Ingestion pipeline | `app/make_samples.py`, `app/crawler.py` |
| 2 LLM extraction, entity resolution | Entities & relations | `app/extract.py`, `app/resolve.py` |
| 3 Spark transformations, Neo4j ingestion | Populated graph | `app/spark_etl.py` |
| 4 Cypher queries, graph algorithms | Analytics module | `app/analytics.py` |
| 5 GraphRAG retrieval and generation | NL graph assistant | `app/graphrag.py` |
| 6 Ground truth, comparative tests | Benchmark report | `data/gold/gold.json`, `app/evaluate.py` |
| 7 API, interface, monitoring, access control | Integrated app | `app/api.py`, `app/static/index.html` |

## Run

Requirements: Docker with Compose, about 6 GB of free RAM. Runs CPU-only.

```bash
docker compose up -d --build                               # HDFS, Neo4j+GDS, Ollama, demo website, app
docker compose exec ollama ollama pull qwen2.5:3b          # once (~2 GB)
docker compose exec app python pipeline.py                 # crawl → extract → resolve → etl → analytics → evaluate
```

Run individual steps with `docker compose exec app python pipeline.py crawl extract resolve etl` (or `analytics`, `evaluate`).
CPU-only, extraction takes about 30 s per document with `qwen2.5:3b`, and the benchmark takes about 15 minutes.

- UI / API: http://localhost:8080 (API key `reader-demo-key`; `admin-demo-key` for `/metrics` and `/admin/retention`)
- Neo4j Browser: http://localhost:7474 (`neo4j` / `graphpass123`)
- HDFS NameNode UI: http://localhost:9870
- Benchmark report: `reports/benchmark.md` (+ raw `benchmark.json`)

### Another LLM

Any OpenAI-compatible endpoint works:

```bash
LLM_BASE_URL=https://api.openai.com/v1 LLM_MODEL=gpt-4o-mini LLM_API_KEY=sk-... docker compose up -d app
# or a larger local model:  LLM_MODEL=qwen2.5:7b  (pull it first)
```

### API

```bash
K='X-API-Key: reader-demo-key'
curl -H "$K" -H 'Content-Type: application/json' -d '{"question":"Who founded Helios Robotics?"}' localhost:8080/ask
curl -H "$K" localhost:8080/analytics/pagerank      # overview | degree | pagerank | betweenness | communities
curl -H "$K" 'localhost:8080/path?a=Mei%20Tanaka&b=Northwind'
curl -H "$K" 'localhost:8080/entity?name=Lumen'
curl -H 'X-API-Key: admin-demo-key' localhost:8080/metrics
curl -X POST -H 'X-API-Key: admin-demo-key' 'localhost:8080/admin/retention?days=365'
```

## Data and ground truth

`data/` contains an **entirely fictional** corpus (companies and people are invented). It has one document per source type:
two crawled web pages plus an index, a JSON news API, a PDF report, a Word brief, an email, a text note, and a SQLite registry.
Facts are spread across documents and named inconsistently ("Dr. Amara Okafor" / "A. Okafor",
"Northwind Capital Partners LLP" / "Northwind"), so multi-hop questions need cross-source entity resolution.

`data/gold/gold.json` holds the gold entities (with aliases), the gold relations, and 12 benchmark questions
(single-hop, multi-hop, aggregation, semantic). Each question has its gold answer, its supporting source documents,
the reference Cypher query (the "graph analytics" approach), and, for multi-hop questions, the gold path.

## Evaluation (research questions of the brief)

| RQ | Measured by |
|---|---|
| 1 LLM extraction accuracy | entity and relation precision/recall/F1 vs gold, before and after Spark schema validation |
| 2 entity resolution | duplicate nodes, fragmentation, gold multi-hop paths actually walkable, QA accuracy on unresolved vs resolved graph |
| 3 GraphRAG vs Cypher | accuracy and answer recall per question type, on the same graph |
| 4 trade-offs | latency per question, LLM tokens (extraction and query time) |
| 5 provenance | share of answers with citations, citation precision vs gold supporting documents |

### Results with `qwen2.5:3b` on CPU (`reports/benchmark.md`)

- **RQ1:** entity F1 0.86 (precision 1.0). Relation F1 0.67 straight from the LLM, 0.71 after Spark schema validation (direction repair lifts precision from 0.69 to 0.78).
- **RQ2:** resolution removes all 6 duplicate nodes (28 → 20) and doubles walkable gold multi-hop paths (1/6 → 2/6). Cypher multi-hop accuracy goes from 0.17 to 0.33.
- **RQ3:** Cypher wins on aggregation and is exact when the edge exists. GraphRAG wins on semantic questions and also answers from entity descriptions when an edge is missing. Both fail when extraction missed the fact (Q2, Q7).
- **RQ4:** Cypher takes about 10 ms and 0 tokens. GraphRAG takes about 57 s and about 1.2k tokens per question on CPU. Extraction costs about 43 s per document.
- **RQ5:** no invalid citations. Citation precision vs gold sources is 0.54 (resolved graph, all questions).
- The scorer is lenient. Reading the answers, GraphRAG's Q10 and Q12 on the resolved graph name the wrong company while mentioning the right one, so the strict GraphRAG accuracy is 5/12, not the 7/12 the table shows.

## Responsible use

Every fact in the graph keeps its source document ids (`sources`), supporting quotes (`evidence`) and a
`confidence`. GraphRAG is instructed to present facts as claims from cited sources, not verified truth, and the UI
says so. API keys enforce reader/admin roles, and every request is audit-logged. `/admin/retention` removes documents
older than `RETENTION_DAYS`, along with any fact or entity no longer backed by a source. If you point the crawler at real data
about identifiable people, only use lawful, authorized sources.

## PoC simplifications (and the upgrade path)

- Spark runs in `local[*]` mode inside the app container. For a cluster, add `spark-master`/`spark-worker` services and set `SPARK_MASTER`.
- One HDFS DataNode with replication 1.
- One LLM call per document (the sample documents are short). Chunk longer documents before extraction.
- GraphRAG seeds come from the Neo4j fulltext index. Add vector embeddings for fuzzier semantic matching.
- The graph is rebuilt from the curated layer on each ETL run. Switch to incremental MERGE for large corpora.
- API keys are static env values. Use OIDC/JWT in production.

`app/test_resolve.py` is a self-check for entity resolution: `docker compose exec app python test_resolve.py`.

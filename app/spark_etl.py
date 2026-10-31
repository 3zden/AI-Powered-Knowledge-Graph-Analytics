"""Phase 3 - Spark ETL: validation, deduplication, transformation, Neo4j ingestion.

    spark-submit spark_etl.py [resolved|unresolved]

hdfs:/extracted/<input>.jsonl -> hdfs:/curated/<input>/{entities,relations} (parquet) -> Neo4j
"""
import json
import os
import sys
from datetime import datetime, timezone

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from common import ENTITY_TYPES, HDFS_FS, neo4j_driver, validate_relation

SCHEMA = [
    "CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
    "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
    "CREATE FULLTEXT INDEX entity_text IF NOT EXISTS FOR (e:Entity) ON EACH [e.name, e.description]",
]


def entity_rows(row):
    for e in row["entities"]:
        if e.get("name") and e.get("type") in ENTITY_TYPES:
            yield (e["name"], e["type"], e.get("description") or "", row["doc_id"])
    for r in relation_rows(row):  # endpoints imply entities even if the LLM didn't list them
        yield (r[0], r[5], "", row["doc_id"])
        yield (r[2], r[6], "", row["doc_id"])


def relation_rows(row):
    for raw in row["relations"]:
        r = validate_relation(raw)
        if r:
            yield (r["source"], r["type"], r["target"], r["evidence"], r["confidence"],
                   r["source_type"], r["target_type"], row["doc_id"])


def _write_partition(query, batch_key):
    def write(rows):
        rows = [r.asDict(recursive=True) for r in rows]
        if not rows:
            return
        groups = {}
        for r in rows:
            groups.setdefault(r[batch_key], []).append(r)
        with neo4j_driver() as d:
            for label, batch in groups.items():
                d.execute_query(query.replace("$LABEL", label), {"rows": batch})
    return write


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "resolved"
    spark = SparkSession.builder.appName(f"kg-etl-{source}").master(os.environ.get("SPARK_MASTER", "local[*]")) \
        .config("spark.ui.showConsoleProgress", "false").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    rows = spark.sparkContext.textFile(f"{HDFS_FS}/extracted/{source}.jsonl").filter(str.strip).map(json.loads)
    raw_rel_count = rows.map(lambda r: len(r["relations"])).sum()

    # validation (schema + direction repair) happens in relation_rows / entity_rows
    mentions = spark.createDataFrame(rows.flatMap(entity_rows), "name string, type string, description string, doc_id string")
    relations = spark.createDataFrame(rows.flatMap(relation_rows),
                                      "source string, type string, target string, evidence string, confidence double, "
                                      "source_type string, target_type string, doc_id string")

    # deduplication: one node per name (majority type), one edge per (source, type, target)
    type_votes = mentions.groupBy("name", "type").count()
    best_type = type_votes.groupBy("name").agg(F.max_by("type", "count").alias("type"))
    entities = mentions.groupBy("name").agg(
        F.max_by("description", F.length("description")).alias("description"),
        F.sort_array(F.collect_set("doc_id")).alias("sources"),
    ).join(best_type, "name")
    edges = relations.groupBy("source", "type", "target").agg(
        F.sort_array(F.collect_set("doc_id")).alias("sources"),
        F.collect_set("evidence").alias("evidence"),
        F.max("confidence").alias("confidence"),
        F.count("*").alias("mentions"),
    )
    entities.write.mode("overwrite").parquet(f"{HDFS_FS}/curated/{source}/entities")
    edges.write.mode("overwrite").parquet(f"{HDFS_FS}/curated/{source}/relations")

    # Neo4j ingestion (graph is rebuilt from the curated layer on every run)
    docs = spark.read.json(f"{HDFS_FS}/parsed/docs.jsonl").select("id", "kind", "source", "title", "text", "fetched_at")
    now = datetime.now(timezone.utc).isoformat()
    with neo4j_driver() as d:
        # ponytail: single-transaction wipe, use CALL {} IN TRANSACTIONS once the graph has millions of nodes
        d.execute_query("MATCH (n) DETACH DELETE n")
        for stmt in SCHEMA:
            d.execute_query(stmt)
        d.execute_query("UNWIND $rows AS r MERGE (d:Document {id: r.id}) SET d += r, d.ingested_at = $now",
                        {"rows": [r.asDict() for r in docs.collect()], "now": now})
    entities.foreachPartition(_write_partition(
        "UNWIND $rows AS r MERGE (e:Entity {name: r.name}) SET e:$LABEL, e.type = r.type, "
        "e.description = r.description, e.sources = r.sources "
        "WITH e, r UNWIND r.sources AS s MATCH (d:Document {id: s}) MERGE (e)-[:MENTIONED_IN]->(d)", "type"))
    edges.foreachPartition(_write_partition(
        "UNWIND $rows AS r MATCH (a:Entity {name: r.source}), (b:Entity {name: r.target}) "
        "MERGE (a)-[x:$LABEL]->(b) SET x.sources = r.sources, x.evidence = r.evidence, "
        "x.confidence = r.confidence, x.mentions = r.mentions", "type"))

    n_ent, n_rel = entities.count(), edges.count()
    print(f"spark_etl[{source}]: {raw_rel_count} extracted relations -> {relations.count()} valid -> "
          f"{n_rel} deduplicated edges; {mentions.count()} entity mentions -> {n_ent} nodes")
    spark.stop()


if __name__ == "__main__":
    main()

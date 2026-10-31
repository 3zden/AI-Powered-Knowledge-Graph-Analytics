"""Phase 4 - structured graph analytics: Cypher queries + Neo4j Graph Data Science algorithms."""
import json

from common import RELATION_TYPES, run_cypher

NOT_MENTION = "type(r) <> 'MENTIONED_IN'"


def overview():
    return {
        "nodes": run_cypher("MATCH (n) RETURN labels(n) AS labels, count(*) AS count ORDER BY count DESC"),
        "relationships": run_cypher("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC"),
    }


def degree(limit=10):
    return run_cypher(f"MATCH (e:Entity)-[r]-(:Entity) WHERE {NOT_MENTION} "
                      "RETURN e.name AS name, e.type AS type, count(r) AS degree ORDER BY degree DESC LIMIT $limit",
                      limit=limit)


def _project():
    types = [r["t"] for r in run_cypher("CALL db.relationshipTypes() YIELD relationshipType AS t RETURN t")
             if r["t"] in RELATION_TYPES]
    run_cypher("CALL gds.graph.drop('kg', false) YIELD graphName RETURN graphName")
    run_cypher("CALL gds.graph.project('kg', 'Entity', $rels) YIELD graphName RETURN graphName",
               rels={t: {"orientation": "UNDIRECTED"} for t in types})


def _stream(proc, limit):
    _project()
    return run_cypher(f"CALL gds.{proc}.stream('kg') YIELD nodeId, score "
                      "WITH gds.util.asNode(nodeId) AS e, score WHERE score > 0 "
                      "RETURN e.name AS name, e.type AS type, round(score, 4) AS score ORDER BY score DESC LIMIT $limit",
                      limit=limit)


def pagerank(limit=10):
    return _stream("pageRank", limit)


def betweenness(limit=10):
    """Brokers: entities sitting on many shortest paths between others."""
    return _stream("betweenness", limit)


def communities():
    _project()
    return run_cypher("CALL gds.louvain.stream('kg') YIELD nodeId, communityId "
                      "WITH communityId, collect(gds.util.asNode(nodeId).name) AS members WHERE size(members) > 1 "
                      "RETURN communityId AS community, size(members) AS size, members ORDER BY size DESC")


def path(a, b):
    """Shortest explainable path between two entities (case-insensitive name match), with provenance."""
    return run_cypher(
        "MATCH (x:Entity) WHERE toLower(x.name) CONTAINS toLower($a) WITH x LIMIT 1 "
        "MATCH (y:Entity) WHERE toLower(y.name) CONTAINS toLower($b) WITH x, y LIMIT 1 "
        "MATCH p = shortestPath((x)-[*..6]-(y)) WHERE all(r IN relationships(p) WHERE type(r) <> 'MENTIONED_IN') "
        "RETURN [r IN relationships(p) | {source: startNode(r).name, type: type(r), target: endNode(r).name, "
        "sources: r.sources}] AS path", a=a, b=b)


def entity(name):
    return run_cypher(
        "MATCH (e:Entity) WHERE toLower(e.name) CONTAINS toLower($name) "
        "OPTIONAL MATCH (e)-[r]-(o:Entity) "
        "RETURN e.name AS name, e.type AS type, e.description AS description, e.sources AS sources, "
        "collect({rel: type(r), other: o.name, outgoing: startNode(r) = e, sources: r.sources}) AS relations",
        name=name)


ANALYSES = {"overview": overview, "degree": degree, "pagerank": pagerank,
            "betweenness": betweenness, "communities": communities}


def main():
    for name, fn in ANALYSES.items():
        print(f"== {name}")
        result = fn()
        for row in (result.items() if isinstance(result, dict) else enumerate(result)):
            print("  ", json.dumps(row[1] if isinstance(result, list) else row, default=str))


if __name__ == "__main__":
    main()

# Benchmark report: Graph Analytics (Cypher) vs GraphRAG
Model: `qwen2.5:3b` - documents: 12 - generated 2026-10-31 17:46:54
## RQ1 - LLM extraction accuracy (lenient alias matching against gold)
| level | precision | recall | f1 | tp | predicted | gold |
|---|---|---|---|---|---|---|
| entities | 1.0 | 0.75 | 0.857 | 18 | 18 | 24 |
| relations_llm_raw | 0.692 | 0.643 | 0.667 | 18 | 26 | 28 |
| relations_after_validation | 0.783 | 0.643 | 0.706 | 18 | 23 | 28 |

`relations_after_validation` = after the Spark schema validation step (type normalization, direction repair).
## RQ2 - Effect of entity resolution
| graph | nodes | nodes_matching_gold | gold_entities_found | duplicate_nodes | fragmentation | multi_hop_paths_supported |
|---|---|---|---|---|---|---|
| unresolved | 28 | 26 | 20 | 6 | 1.3 | 1/6 |
| resolved | 20 | 20 | 20 | 0 | 1.0 | 2/6 |

## RQ3 - Cypher vs GraphRAG by question type
Scoring is mention-based: an answer gets credit for each gold item it names. This is lenient for free-text GraphRAG answers (an answer that mentions the gold entity while picking another one still scores), so check the per-question table below.

| graph | type | approach | n | accuracy | mean_recall | mean_latency_s | mean_tokens | citation_precision |
|---|---|---|---|---|---|---|---|---|
| unresolved | all | cypher | 12 | 0.5 | 0.542 | 0.088 |  |  |
| unresolved | all | graphrag | 12 | 0.583 | 0.625 | 54.138 | 1007 | 0.792 |
| unresolved | aggregation | cypher | 1 | 1.0 | 1.0 | 0.074 |  |  |
| unresolved | aggregation | graphrag | 1 | 0.0 | 0.0 | 5.891 | 780 | 1.0 |
| unresolved | multi-hop | cypher | 6 | 0.167 | 0.25 | 0.102 |  |  |
| unresolved | multi-hop | graphrag | 6 | 0.5 | 0.583 | 62.779 | 1089 | 0.583 |
| unresolved | semantic | cypher | 2 | 1.0 | 1.0 | 0.089 |  |  |
| unresolved | semantic | graphrag | 2 | 0.5 | 0.5 | 49.047 | 856 | 1.0 |
| unresolved | single-hop | cypher | 3 | 0.667 | 0.667 | 0.061 |  |  |
| unresolved | single-hop | graphrag | 3 | 1.0 | 1.0 | 56.332 | 1020 | 1.0 |
| resolved | all | cypher | 12 | 0.5 | 0.542 | 0.011 |  |  |
| resolved | all | graphrag | 12 | 0.583 | 0.625 | 57.185 | 1178 | 0.544 |
| resolved | aggregation | cypher | 1 | 1.0 | 1.0 | 0.01 |  |  |
| resolved | aggregation | graphrag | 1 | 0.0 | 0.0 | 9.682 | 1268 | 1.0 |
| resolved | multi-hop | cypher | 6 | 0.333 | 0.417 | 0.011 |  |  |
| resolved | multi-hop | graphrag | 6 | 0.5 | 0.583 | 67.485 | 1273 | 0.389 |
| resolved | semantic | cypher | 2 | 0.5 | 0.5 | 0.013 |  |  |
| resolved | semantic | graphrag | 2 | 1.0 | 1.0 | 40.535 | 868 | 0.6 |
| resolved | single-hop | cypher | 3 | 0.667 | 0.667 | 0.009 |  |  |
| resolved | single-hop | graphrag | 3 | 0.667 | 0.667 | 63.518 | 1165 | 0.667 |

## RQ4 - Cost and latency
- Extraction: 11768 tokens, 516.8 s LLM time (43.1 s/doc).
- Cypher queries: 0 tokens at query time; GraphRAG: see `mean_tokens` above.

## RQ5 - Provenance
Every node and edge stores the ids of the documents it was extracted from (`sources`) and the supporting quotes (`evidence`). GraphRAG answers cite these ids; `citation_precision` is the share of cited sources that are gold supporting documents for the question. Invalid citations (ids not present in the retrieved context): 0.

## Per-question results (resolved graph)
| id | type | cypher | graphrag | rag_answer |
|---|---|---|---|---|
| Q1 | single-hop | 1.0 | 1.0 | Amara Okafor founded Helios Robotics, as indicated by the confidence level of 0.95 in the web:helios.html document. [web:helios.html] |
| Q2 | single-hop | 0.0 | 0.0 | Verdant Grid is headquartered in Montreal, as stated in the fact that Lumen Batteries, a portfolio company of Northwind Capital and based in Turin, supplies Ver |
| Q3 | single-hop | 1.0 | 1.0 | Northwind Capital Partners has invested in Lumen Batteries, Helios Robotics, and Verdant Grid AS based on the provided facts. [pdf:northwind_report.pdf] |
| Q4 | aggregation | 1.0 | 0.0 | Northwind Capital Partners has invested in at least four companies based on the provided facts: Lumen Batteries, Helios Robotics, Verdant Grid, and Quanta Bio.  |
| Q5 | multi-hop | 0.0 | 1.0 | Lumen Batteries supplies Helios Robotics and is based in Turin. [api:news.json#2, docx:lumen_brief.docx] |
| Q6 | multi-hop | 0.0 | 0.0 | Amara Okafor leads Helios Robotics, which partners with Corvid Analytics.  [web:helios.html] |
| Q7 | multi-hop | 0.0 | 0.0 | Julien Moreau, who is a board member of Lumen Batteries and also serves on the board of Helios Robotics, which is a portfolio company of Northwind Capital Partn |
| Q8 | multi-hop | 1.0 | 1.0 | Mei Tanaka founded Corvid Analytics, which partners with the developer of GridMind, Verdant Grid AS. [db:registry.sqlite#companies/1, api:news.json#1, web:verda |
| Q9 | multi-hop | 0.5 | 0.5 | Kestrel Sensors was acquired by Verdant Grid AS in 2023, and Verdant Grid AS is based in Basel. [web:verdant.html, db:registry.sqlite#companies/0] |
| Q10 | multi-hop | 1.0 | 1.0 | Northwind Capital invested in Helios Robotics and Lumen Batteries, and Helios Robotics supplies Lumen Batteries, indicating that Helios Robotics is the company  |
| Q11 | semantic | 0.0 | 1.0 | Lumen Batteries develops SolidCell, a solid-state energy storage cell. [docx:lumen_brief.docx] |
| Q12 | semantic | 1.0 | 1.0 | Helios Robotics automates biotech laboratories with robots, as evidenced by the fact that Helios Robotics PARTNERS_WITH Quanta Bio and Amara Okafor LEADS Helios |

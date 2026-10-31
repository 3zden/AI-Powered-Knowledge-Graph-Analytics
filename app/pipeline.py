"""End-to-end runner:  python pipeline.py [crawl extract resolve etl analytics evaluate]  (default: all)."""
import subprocess
import sys

import analytics
import crawler
import evaluate
import extract
import resolve

STEPS = {
    "crawl": crawler.main,
    "extract": extract.main,
    "resolve": resolve.main,
    "etl": lambda: subprocess.run(["spark-submit", "--master", "local[*]", "spark_etl.py", "resolved"], check=True),
    "analytics": analytics.main,
    "evaluate": evaluate.main,
}

if __name__ == "__main__":
    for step in sys.argv[1:] or STEPS:
        print(f"==> {step}")
        STEPS[step]()

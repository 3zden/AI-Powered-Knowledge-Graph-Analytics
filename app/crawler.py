"""Phase 1 - collect heterogeneous sources, parse them to text + metadata, land them in HDFS.

HDFS layout:  /raw/<kind>/<name>  (original bytes)   /parsed/docs.jsonl  (one parsed doc per line)
"""
import hashlib
import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from docx import Document
from pypdf import PdfReader

from common import hdfs_put, hdfs_put_jsonl

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
MAX_PAGES = 50


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.links, self.title, self._skip, self._in_title = [], [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        self._in_title = tag == "title"
        if tag == "a" and dict(attrs).get("href"):
            self.links.append(dict(attrs)["href"])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1
        self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()
        elif not self._skip and data.strip():
            self.text.append(data.strip())


def parse_html(raw):
    p = _HTMLText()
    p.feed(raw.decode("utf-8", "replace"))
    return p.title, "\n".join(p.text), p.links


def parse_file(path, raw):
    """Returns (title, text) for a local file, by extension."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return path.stem, "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
    if ext == ".docx":
        return path.stem, "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
    if ext == ".eml":
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        header = f"From: {msg['from']}\nTo: {msg['to']}\nDate: {msg['date']}\nSubject: {msg['subject']}\n\n"
        return msg["subject"], header + msg.get_body(preferencelist=("plain",)).get_content()
    if ext in (".html", ".htm"):
        title, text, _ = parse_html(raw)
        return title or path.stem, text
    return path.stem, raw.decode("utf-8", "replace")


def make_doc(doc_id, kind, source, title, text, raw, **meta):
    return {"id": doc_id, "kind": kind, "source": source, "title": title, "text": text,
            "sha256": hashlib.sha256(raw).hexdigest(), "fetched_at": datetime.now(timezone.utc).isoformat(),
            "metadata": meta}


def crawl_web(seeds):
    """Breadth-first crawl restricted to the seeds' hosts."""
    hosts = {urlparse(s).netloc for s in seeds}
    queue, seen = list(seeds), set()
    while queue and len(seen) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        r = requests.get(url, timeout=30)
        if not r.ok:
            continue
        title, text, links = parse_html(r.content)
        name = Path(urlparse(url).path).name or "index.html"
        yield make_doc(f"web:{name}", "web", url, title, text, r.content,
                       content_type=r.headers.get("content-type", "")), r.content
        queue += [u for u in (urljoin(url, link).split("#")[0] for link in links) if urlparse(u).netloc in hosts]


def crawl_api(urls):
    """Each item of a JSON array becomes one document."""
    for url in urls:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        name = Path(urlparse(url).path).name
        for i, item in enumerate(r.json()):
            raw = json.dumps(item).encode()
            text = "\n".join(f"{k}: {v}" for k, v in item.items())
            yield make_doc(f"api:{name}#{i}", "api", url, item.get("headline", name), text, raw), raw


def crawl_files(root):
    for path in sorted(root.rglob("*")):
        if path.is_file():
            raw = path.read_bytes()
            title, text = parse_file(path, raw)
            yield make_doc(f"{path.suffix.lstrip('.')}:{path.name}", path.suffix.lstrip("."), str(path),
                           title, text, raw, size=len(raw)), raw


def crawl_sqlite(root):
    """Each row of each table becomes one 'column: value' document."""
    for db in sorted(root.glob("*.sqlite")):
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                for i, row in enumerate(con.execute(f'SELECT * FROM "{table}"')):
                    text = "\n".join(f"{k}: {row[k]}" for k in row.keys())
                    raw = text.encode()
                    yield make_doc(f"db:{db.name}#{table}/{i}", "db", f"{db}#{table}", f"{table} row {i}", text, raw), raw


def main():
    sources = [
        crawl_web(os.environ.get("SEED_URLS", "").split()),
        crawl_api(os.environ.get("API_URLS", "").split()),
        crawl_files(DATA_DIR / "files"),
        crawl_sqlite(DATA_DIR / "db"),
    ]
    docs, hashes = [], set()
    for source in sources:
        for doc, raw in source:
            if doc["sha256"] in hashes or not doc["text"].strip():
                continue
            hashes.add(doc["sha256"])
            hdfs_put(f"/raw/{doc['kind']}/{doc['id'].split(':', 1)[1].replace('/', '_')}", raw)
            docs.append(doc)
    hdfs_put_jsonl("/parsed/docs.jsonl", docs)
    for d in docs:
        print(f"  {d['id']:<35} {len(d['text']):>5} chars")
    print(f"crawler: {len(docs)} documents -> hdfs:/parsed/docs.jsonl")


if __name__ == "__main__":
    main()

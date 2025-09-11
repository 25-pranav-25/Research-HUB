import json
import sqlite3
from typing import Iterable, List, Optional, Tuple, Union
from config import DB_FILE


def get_connection() -> sqlite3.Connection:
    """
    Open a SQLite connection with foreign keys enabled.
    """
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def create_tables() -> None:
    """
    Create all tables if they don't exist.
    Schema:
      - papers      : raw metadata
      - summaries   : short + long
      - facts       : structured insights (Problem/Approach/Result/Limitations/etc.)
      - entities    : datasets/models/techniques
      - mindmaps    : JSON for visualization
    """
    conn = get_connection()
    cur = conn.cursor()

    # papers
    cur.execute("""
    CREATE TABLE IF NOT EXISTS papers (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        title           TEXT NOT NULL,
        authors         TEXT,
        abstract        TEXT,
        pdf_url         TEXT UNIQUE,            -- prefer de-dup by URL
        source          TEXT,                   -- e.g., 'arXiv'
        published_date  TEXT,
        keyword         TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(title, source)                   -- also prevent dup by (title, source)
    );
    """)

    # summaries (one row per paper)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS summaries (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id       INTEGER UNIQUE,
        summary_short  TEXT,
        summary_long   TEXT,
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
    );
    """)

    # facts (multiple rows per paper)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS facts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id   INTEGER,
        fact_type  TEXT,    -- e.g., 'Problem','Approach','Key Result','Limitation','Why it matters'
        fact_value TEXT,
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
    );
    """)

    # entities (multiple rows per paper; prevent exact dup per paper)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS entities (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id     INTEGER,
        entity       TEXT,
        entity_type  TEXT,  -- e.g., 'Dataset','Model','Method','Tool'
        UNIQUE(paper_id, entity, entity_type),
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
    );
    """)

    # mindmaps (one row per paper)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mindmaps (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id      INTEGER UNIQUE,
        mindmap_json  TEXT,
        FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
    );
    """)

    # helpful indices
    cur.execute("CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published_date DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_facts_paper ON facts(paper_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entities_paper ON entities(paper_id);")

    conn.commit()
    conn.close()


# ---------- UPSERT HELPERS ----------

def upsert_paper(
    *,
    title: str,
    authors: Optional[str],
    abstract: Optional[str],
    pdf_url: Optional[str],
    source: Optional[str],
    published_date: Optional[str],
    keyword: Optional[str] = None
) -> int:
    """
    Insert a paper if new; otherwise return existing id.
    If the paper exists and some fields are missing in DB but provided here, they get backfilled.
    Returns: paper_id
    """
    conn = get_connection()
    cur = conn.cursor()

    # Try insert first
    try:
        cur.execute("""
            INSERT INTO papers (title, authors, abstract, pdf_url, source, published_date,keyword)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (title, authors, abstract, pdf_url, source, published_date,keyword))
        conn.commit()
        paper_id = cur.lastrowid
        conn.close()
        return paper_id
    except sqlite3.IntegrityError:
        # De-dup path: fetch by url OR by (title, source)
        if pdf_url:
            cur.execute("SELECT id, authors, abstract, published_date FROM papers WHERE pdf_url = ?", (pdf_url,))
            row = cur.fetchone()
        else:
            cur.execute("SELECT id, authors, abstract, published_date FROM papers WHERE title = ? AND source = ?", (title, source))
            row = cur.fetchone()

        if not row:
            # Fallback: try by title alone
            cur.execute("SELECT id, authors, abstract, published_date FROM papers WHERE title = ?", (title,))
            row = cur.fetchone()

        if not row:
            # Very rare: constraint fired but we couldn't find row; re-raise
            conn.close()
            raise

        paper_id, db_authors, db_abstract, db_pub = row

        # Backfill missing data if provided now
        updates = []
        params = []

        if (not db_authors) and authors:
            updates.append("authors = ?")
            params.append(authors)
        if (not db_abstract) and abstract:
            updates.append("abstract = ?")
            params.append(abstract)
        if (not db_pub) and published_date:
            updates.append("published_date = ?")
            params.append(published_date)

        if updates:
            params.append(paper_id)
            cur.execute(f"UPDATE papers SET {', '.join(updates)} WHERE id = ?", tuple(params))
            conn.commit()

        conn.close()
        return paper_id


def upsert_summaries(paper_id: int, summary_short: Optional[str], summary_long: Optional[str]) -> None:
    """
    Insert or update summaries for a paper (one row per paper).
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT id FROM summaries WHERE paper_id = ?", (paper_id,))
    row = cur.fetchone()

    if row:
        sets = []
        params = []
        if summary_short is not None:
            sets.append("summary_short = ?")
            params.append(summary_short)
        if summary_long is not None:
            sets.append("summary_long = ?")
            params.append(summary_long)
        if sets:
            params.append(paper_id)
            cur.execute(f"UPDATE summaries SET {', '.join(sets)} WHERE paper_id = ?", tuple(params))
    else:
        cur.execute("""
            INSERT INTO summaries (paper_id, summary_short, summary_long)
            VALUES (?, ?, ?)
        """, (paper_id, summary_short, summary_long))

    conn.commit()
    conn.close()


def replace_facts(paper_id: int, facts: Iterable[Tuple[str, str]]) -> None:
    """
    Replace all facts for a paper with the provided list.
    facts: iterable of (fact_type, fact_value)
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM facts WHERE paper_id = ?", (paper_id,))
    cur.executemany(
        "INSERT INTO facts (paper_id, fact_type, fact_value) VALUES (?, ?, ?)",
        [(paper_id, ftype, fval) for ftype, fval in facts]
    )
    conn.commit()
    conn.close()


def upsert_entities(paper_id: int, entities: Iterable[Tuple[str, str]]) -> None:
    """
    Insert entities for a paper. Ignores exact duplicates.
    entities: iterable of (entity, entity_type)
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        "INSERT OR IGNORE INTO entities (paper_id, entity, entity_type) VALUES (?, ?, ?)",
        [(paper_id, ent, ent_type) for ent, ent_type in entities]
    )
    conn.commit()
    conn.close()


def upsert_mindmap(paper_id: int, mindmap: Union[str, dict, list]) -> None:
    """
    Insert or update mindmap JSON for a paper (one row per paper).
    mindmap: JSON string or python dict/list (will be json.dumps-ed)
    """
    if not isinstance(mindmap, str):
        mindmap_json = json.dumps(mindmap, ensure_ascii=False)
    else:
        mindmap_json = mindmap

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT id FROM mindmaps WHERE paper_id = ?", (paper_id,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE mindmaps SET mindmap_json = ? WHERE paper_id = ?", (mindmap_json, paper_id))
    else:
        cur.execute("INSERT INTO mindmaps (paper_id, mindmap_json) VALUES (?, ?)", (paper_id, mindmap_json))

    conn.commit()
    conn.close()


# ---------- QUERY HELPERS ----------

import sqlite3

DB_NAME = "papers.db"

import sqlite3

def list_papers(page=1, per_page=10):
    """Fetch paginated papers with short summaries (consistent with get_full_paper)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    offset = (page - 1) * per_page

    cursor.execute("""
        SELECT p.id, p.title, p.authors, s.summary_short, p.published_date, p.source
        FROM papers p
        LEFT JOIN summaries s ON p.id = s.paper_id
        ORDER BY p.published_date DESC
        LIMIT ? OFFSET ?
    """, (per_page, offset))

    papers = cursor.fetchall()

    # Fetch total count for pagination
    cursor.execute("SELECT COUNT(*) FROM papers")
    total_papers = cursor.fetchone()[0]

    conn.close()

    # Format into list of dicts (nicer for API response)
    formatted = [
        {
            "id": row[0],
            "title": row[1],
            "authors": row[2],
            "summary_short": row[3],
            "published_date": row[4],
            "source": row[5],
        }
        for row in papers
    ]

    return formatted, total_papers




def get_paper_by_id(paper_id: int) -> Optional[Tuple]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM papers WHERE id = ?", (paper_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_paper_by_title(title: str) -> Optional[Tuple]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM papers WHERE title = ?", (title,))
    row = cur.fetchone()
    conn.close()
    return row


def get_full_paper(paper_id: int) -> dict:
    """
    Return a dict with paper + summaries + facts + entities + mindmap.
    """
    conn = get_connection()
    cur = conn.cursor()

    # Fetch paper details
    cur.execute("SELECT id, title, authors, abstract, pdf_url, source, published_date FROM papers WHERE id = ?", (paper_id,))
    paper = cur.fetchone()

    # Fetch summaries
    cur.execute("SELECT summary_short, summary_long FROM summaries WHERE paper_id = ?", (paper_id,))
    summaries = cur.fetchone()

    # Fetch facts
    cur.execute("SELECT fact_type, fact_value FROM facts WHERE paper_id = ?", (paper_id,))
    facts = cur.fetchall()

    # Fetch entities
    cur.execute("SELECT entity, entity_type FROM entities WHERE paper_id = ?", (paper_id,))
    entities = cur.fetchall()

    # Fetch mindmap
    cur.execute("SELECT mindmap_json FROM mindmaps WHERE paper_id = ?", (paper_id,))
    mm = cur.fetchone()

    conn.close()

    if not paper:
        return None

    return {
        "id": paper[0],
        "paper": {
            "title": paper[1],
            "authors": paper[2],
            "abstract": paper[3],
            "pdf_url": paper[4],
            "source": paper[5],
            "published_date": paper[6],
        },
        "summaries": {
            "short": summaries[0] if summaries else None,
            "long": summaries[1] if summaries else None,
        },
        "facts": [{"type": f[0], "value": f[1]} for f in facts],
        "entities": [{"entity": e[0], "type": e[1]} for e in entities],
        "mindmap_json": mm[0] if mm else None,
    }

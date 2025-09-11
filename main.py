# main.py
import argparse
from typing import Optional, List, Dict

from config import SEARCH_KEYWORDS
from scraper import fetch_papers
from db_utils import create_tables, upsert_paper, get_connection, get_full_paper
from summarizer import run_summarizer


def _find_existing_paper_id(title: str, pdf_url: Optional[str], source: str = "arXiv") -> Optional[int]:
    """
    Check if a paper already exists by pdf_url or (title, source).
    Returns the paper_id if found, else None.
    """
    conn = get_connection()
    cur = conn.cursor()

    if pdf_url:
        cur.execute("SELECT id FROM papers WHERE pdf_url = ?", (pdf_url,))
        row = cur.fetchone()
        if row:
            conn.close()
            return row[0]

    cur.execute("SELECT id FROM papers WHERE title = ? AND source = ?", (title, source))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def run_fetch_flow():
    """Fetch, insert, and summarize new papers."""
    print("🚀 Initializing database...")
    create_tables()

    print("\n📥 Fetching latest papers from arXiv...")
    papers: List[Dict] = fetch_papers(SEARCH_KEYWORDS, max_results=3)
    print(f"Found {len(papers)} candidate papers.")

    new_ids = []
    existing = 0

    for p in papers:
        title = p["title"]
        pdf_url = p.get("pdf_url")
        source = "arXiv"

        preexisting_id = _find_existing_paper_id(title, pdf_url, source)

        paper_id = upsert_paper(
            title=title,
            authors=p.get("authors"),
            abstract=p.get("abstract"),
            pdf_url=pdf_url,
            source=source,
            published_date=p.get("published"),
            keyword=p.get("keyword"),
        )

        if preexisting_id is None:
            new_ids.append(paper_id)
            print(f"✅ Inserted NEW: {title[:80]} (id={paper_id})")
        else:
            existing += 1
            print(f"↩️  Already present: {title[:80]} (id={paper_id}) — skipping summarization")

    if new_ids:
        print(f"\n📝 Running summarizer for {len(new_ids)} new papers...")
        for pid in new_ids:
            run_summarizer(pid)
    else:
        print("\n📝 No new papers to summarize.")

    print(f"\n📊 Done. New: {len(new_ids)} | Existing: {existing}")


def run_view_flow(paper_id: int):
    """Display a saved paper with all details in a pretty format."""
    full = get_full_paper(paper_id)

    if not full or not full["paper"]:
        print(f"❌ No paper found with ID {paper_id}")
        return

    paper = full["paper"]
    summaries = full["summaries"]
    facts = full["facts"]
    entities = full["entities"]
    mindmap_json = full["mindmap_json"]

    print("\n📄 Paper Details")
    print("=" * 60)
    print(f"🆔 ID: {full['id']}")
    print(f"📌 Title: {paper['title']}")
    print(f"👨‍🔬 Authors: {paper['authors']}")
    print(f"🔗 PDF: {paper['pdf_url']}")

    print("\n📝 Summaries")
    print("-" * 60)
    if summaries:
        print(f"➡️ Short: {summaries['short']}")
        print(f"📖 Long: {summaries['long']}")
    else:
        print("❌ No summaries available.")

    print("\n📊 Facts")
    print("-" * 60)
    if facts:
        for f in facts:
            print(f"• {f['fact_type']}: {f['fact_value']}")
    else:
        print("❌ No facts extracted.")

    print("\n🔎 Entities")
    print("-" * 60)
    if entities:
        for e in entities:
            print(f"• {e['entity']} ({e['entity_type']})")
    else:
        print("❌ No entities found.")

    print("\n🧠 Mindmap")
    print("-" * 60)
    if mindmap_json:
        print(mindmap_json)
    else:
        print("❌ No mindmap available.")



def main():
    parser = argparse.ArgumentParser(
        description="📚 Agentic AI Research Hub CLI"
    )
    parser.add_argument(
        "--fetch", action="store_true",
        help="Fetch latest papers and summarize new ones"
    )
    parser.add_argument(
        "--view", type=int, metavar="PAPER_ID",
        help="View a paper by its database ID"
    )

    args = parser.parse_args()

    if args.view:
        run_view_flow(args.view)
    elif args.fetch:
        run_fetch_flow()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

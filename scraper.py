import arxiv
from arxiv import UnexpectedEmptyPageError

def fetch_papers(keywords, max_results=5):
    """
    Fetch papers from arXiv for the given keywords.

    Args:
        keywords (list[str]): List of keywords to search for.
        max_results (int): Max results per keyword.

    Returns:
        list[dict]: List of paper metadata dicts.
    """
    papers = []
    for kw in keywords:
        print(f"\n🔎 Searching for: {kw}")
        try:
            search = arxiv.Search(
                query=kw,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.SubmittedDate
            )

            for result in search.results():
                papers.append({
                    "arxiv_id": result.entry_id.split("/")[-1],
                    "title": result.title.strip(),
                    "authors": ", ".join([a.name for a in result.authors]),
                    "abstract": result.summary.strip(),
                    "published": result.published.strftime("%Y-%m-%d"),
                    "pdf_url": result.pdf_url,
                    "source_url": result.entry_id,
                    "keyword": kw   # 🔑 track which keyword matched
                })

        except UnexpectedEmptyPageError:
            print(f"⚠️ No results for keyword: {kw}")
            continue  # skip to the next keyword
        except Exception as e:
            print(f"❌ Error fetching papers for '{kw}': {e}")
            continue

    return papers

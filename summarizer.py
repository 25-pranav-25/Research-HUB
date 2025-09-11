import json
from typing import Dict, List
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from google import genai
from google.genai import types
from config import GEMINI_API_KEY
from db_utils import (
    upsert_summaries,
    replace_facts,
    upsert_entities,
    upsert_mindmap,
    get_connection,
)

# ---- Gemini with Google Search ----
client = genai.Client(api_key=GEMINI_API_KEY)
grounding_tool = types.Tool(google_search=types.GoogleSearch())
config = types.GenerateContentConfig(tools=[grounding_tool])

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GEMINI_API_KEY,
    generation_config=config,
    temperature=0.2,
)

# ---- LangGraph State ----
class PaperState(Dict):
    paper_id: int
    title: str
    abstract: str
    summaries: Dict
    facts: List
    entities: List
    mindmap: str


# --- Nodes ---
def summarize_node(state: PaperState):
    prompt = f"""
You are an expert AI research summarizer.

Task:
1. Search the web for this paper (use title/URL).
2. Write:
   - **Short summary** (≤3 sentences, quick preview).
   - **Long summary** (2–3 paragraphs, plain English, covering motivation, methods, findings).
3. Use only grounded evidence. Avoid hallucinations.

Title: {state['title']}
Abstract: {state['abstract']}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    text = resp.content.strip()

    # Split heuristically
    if "Long Summary:" in text:
        parts = text.split("Long Summary:")
        short = parts[0].replace("Short Summary:", "").strip()
        long = parts[1].strip()
    else:
        lines = text.split("\n")
        short = " ".join(lines[:3]) if len(lines) >= 3 else text[:250]
        long = text

    state["summaries"] = {"short": short, "long": long}
    return state


def facts_node(state: PaperState):
    prompt = f"""
You are an AI fact extractor.

Steps:
1. Search the web for the given paper (title/URL).
2. Extract **key research facts** as JSON array of objects with "type" and "value".

Fact types required:
- Problem
- Approach
- Key Result
- Limitation
- Why it matters

Rules:
- Keep each value 1–2 sentences max.
- Be precise (include numbers/datasets when available).
- Do NOT copy summaries.

Title: {state['title']}
Abstract: {state['abstract']}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    try:
        state["facts"] = json.loads(resp.content)
    except:
        state["facts"] = []
    return state


def entities_node(state: PaperState):
    prompt = f"""
You are an AI entity extractor.

Steps:
1. Search the web for this paper (title/URL).
2. Identify **explicit technical entities**.
3. Return JSON array of objects with:
   - "entity": name
   - "type": one of ["Dataset","Model","Method","Tool","Institution"]

Examples:
[
  {{"entity":"ResNet-50","type":"Model"}},
  {{"entity":"CIFAR-10","type":"Dataset"}},
  {{"entity":"Reinforcement Learning","type":"Method"}}
]

Title: {state['title']}
Abstract: {state['abstract']}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    try:
        state["entities"] = json.loads(resp.content)
    except:
        state["entities"] = []
    return state


def mindmap_node(state: PaperState):
    prompt = f"""
You are an AI mindmap generator.

Steps:
1. Search the web for this paper (title/URL).
2. Build a **hierarchical JSON mindmap**.

Schema:
{{
  "nodes": [{{"id":"n1","label":"Problem"}}, ...],
  "edges": [{{"from":"n1","to":"n2"}}, ...]
}}

Rules:
- Root: Paper Title
- Children: Problem, Motivation, Approach, Experiments, Results, Applications, Limitations, Future Work
- Each child can have nested nodes (keep labels 3–7 words).
- Be concise & factual.

Title: {state['title']}
Abstract: {state['abstract']}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    state["mindmap"] = resp.content.strip()
    return state


# --- Graph ---
graph = StateGraph(PaperState)
graph.add_node("summarize", summarize_node)
graph.add_node("facts", facts_node)
graph.add_node("entities", entities_node)
graph.add_node("mindmap", mindmap_node)

graph.set_entry_point("summarize")
graph.add_edge("summarize", "facts")
graph.add_edge("facts", "entities")
graph.add_edge("entities", "mindmap")
graph.add_edge("mindmap", END)

pipeline = graph.compile()


# ---- Core Runner ----
def _summarize_and_store(paper_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, authors, abstract, pdf_url FROM papers WHERE id = ?", (paper_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        print(f"❌ Paper {paper_id} not found in DB.")
        return

    pid, title, authors, abstract, pdf = row
    print(f"\n>>> Summarizing: {title[:60]}...")

    state = {"paper_id": pid, "title": title, "abstract": abstract, "pdf_url": pdf}
    final = pipeline.invoke(state)

    # Save into DB
    upsert_summaries(pid, final["summaries"]["short"], final["summaries"]["long"])
    facts = [(f["type"], f["value"]) for f in final["facts"] if "type" in f and "value" in f]
    replace_facts(pid, facts)
    entities = [(e["entity"], e["type"]) for e in final["entities"] if "entity" in e and "type" in e]
    upsert_entities(pid, entities)
    upsert_mindmap(pid, final["mindmap"])

    print(f"✔️ Done summarizing {title[:40]}...")


def process_papers(limit=3):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id FROM papers p
        LEFT JOIN summaries s ON p.id = s.paper_id
        WHERE s.paper_id IS NULL
        ORDER BY p.published_date DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()

    for (pid,) in rows:
        _summarize_and_store(pid)


def run_summarizer(paper_id: int):
    _summarize_and_store(paper_id)


if __name__ == "__main__":
    process_papers()

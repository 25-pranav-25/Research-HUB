import json
from typing import Dict, List
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

# ---- State ----
class PaperState(Dict):
    paper_id: int
    title: str
    abstract: str
    summaries: Dict
    facts: List
    entities: List
    mindmap: str


# -------------------
# Tool Functions
# -------------------
def summarize_tool(state: PaperState):
    prompt = f"""
You are an expert AI research summarizer.

Task:
1. Search the web for this paper (use title/URL).
2. Write:
   - **Short summary** (≤3 sentences).
   - **Long summary** (2–3 paragraphs, covering motivation, methods, findings).

Title: {state['title']}
Abstract: {state['abstract']}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    text = resp.content.strip()

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


def facts_tool(state: PaperState):
    prompt = f"""
Extract **key research facts** as JSON array of objects with "type" and "value".

Fact types required:
- Problem
- Approach
- Key Result
- Limitation
- Why it matters

Title: {state['title']}
Abstract: {state['abstract']}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    try:
        state["facts"] = json.loads(resp.content)
    except:
        state["facts"] = []
    return state


def entities_tool(state: PaperState):
    prompt = f"""
Identify explicit technical entities from this paper.

Return JSON array of objects:
- "entity": name
- "type": one of ["Dataset","Model","Method","Tool","Institution"]

Title: {state['title']}
Abstract: {state['abstract']}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    try:
        state["entities"] = json.loads(resp.content)
    except:
        state["entities"] = []
    return state


def mindmap_tool(state: PaperState):
    prompt = f"""
Generate a **hierarchical JSON mindmap**.

Schema:
{{
  "nodes": [{{"id":"n1","label":"Problem"}}, ...],
  "edges": [{{"from":"n1","to":"n2"}}, ...]
}}

Rules:
- Root: Paper Title
- Children: Problem, Motivation, Approach, Experiments, Results, Applications, Limitations, Future Work
- Keep labels 3–7 words.

Title: {state['title']}
Abstract: {state['abstract']}
"""
    resp = llm.invoke([{"role": "user", "content": prompt}])
    state["mindmap"] = resp.content.strip()
    return state


# -------------------
# ReAct Agent Loop
# -------------------
TOOLS = {
    "summarize": summarize_tool,
    "facts": facts_tool,
    "entities": entities_tool,
    "mindmap": mindmap_tool,
    "finish": lambda s: s
}


def agent_loop(state: PaperState):
    while True:
        # Ask model what to do next
        prompt = f"""
You are an Agentic AI assistant for summarizing research papers.

Paper: {state['title']}

Available tools: {list(TOOLS.keys())}

Current state:
- Summaries: {bool(state.get("summaries"))}
- Facts: {len(state.get("facts", []))}
- Entities: {len(state.get("entities", []))}
- Mindmap: {bool(state.get("mindmap"))}

Think step by step. Which tool should you call next?
Answer ONLY in JSON: {{"action": "<tool_name>"}}
"""
        resp = llm.invoke([{"role": "user", "content": prompt}])
        try:
            decision = json.loads(resp.content)
            action = decision.get("action", "finish")
        except:
            action = "finish"

        if action not in TOOLS:
            action = "finish"

        if action == "finish":
            break

        # Run the chosen tool
        state = TOOLS[action](state)

    return state


# -------------------
# Core Runner
# -------------------
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
    print(f"\n>>> Processing: {title[:60]}...")

    state = {"paper_id": pid, "title": title, "abstract": abstract, "pdf_url": pdf,
             "summaries": {}, "facts": [], "entities": [], "mindmap": ""}

    final = agent_loop(state)

    # Save into DB
    if final["summaries"]:
        upsert_summaries(pid, final["summaries"]["short"], final["summaries"]["long"])
    if final["facts"]:
        facts = [(f["type"], f["value"]) for f in final["facts"] if "type" in f and "value" in f]
        replace_facts(pid, facts)
    if final["entities"]:
        entities = [(e["entity"], e["type"]) for e in final["entities"] if "entity" in e and "type" in e]
        upsert_entities(pid, entities)
    if final["mindmap"]:
        upsert_mindmap(pid, final["mindmap"])

    print(f"✔️ Done processing {title[:40]}...")


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

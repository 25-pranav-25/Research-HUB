const API_BASE = "/api";

// ------------------
// Helpers
// ------------------
function cleanSummary(text) {
  if (!text) return "";
  return text
    .replace(/^Here are the summaries based on the provided abstract:\s*/i, "")
    .replace(/^#+\s*/gm, "")
    .replace(/\*\*/g, "");
}

function formatSummaries(text, type = "short") {
  if (!text) return "";
  text = text.replace(/^Here are the summaries based on the provided abstract:\s*/i, "");

  const shortMatch = text.match(/Short Summary\s*(.+?)(?=Long Summary|$)/is);
  const longMatch = text.match(/Long Summary\s*(.+)$/is);

  if (type === "short" && shortMatch) {
    return `<strong>Short Summary:</strong> ${shortMatch[1].trim()}`;
  }
  if (type === "long" && longMatch) {
    return ` ${longMatch[1].trim()}`;
  }
  return text.trim();
}

// Convert {nodes, edges} → hierarchy for D3
function convertToHierarchy(graph) {
  const nodesById = {};
  graph.nodes.forEach(n => {
    nodesById[n.id] = { name: n.label, children: [] };
  });

  graph.edges.forEach(e => {
    if (nodesById[e.from] && nodesById[e.to]) {
      nodesById[e.from].children.push(nodesById[e.to]);
    }
  });

  // Pick the "root" = node with no incoming edges
  const targets = new Set(graph.edges.map(e => e.to));
  const rootNode = graph.nodes.find(n => !targets.has(n.id)) || graph.nodes[0];

  return nodesById[rootNode.id];
}

// ------------------
// Home Page
// ------------------
async function loadPapers(page = 1) {
  const res = await fetch(`${API_BASE}/papers?page=${page}&per_page=12`);
  const data = await res.json();
  const grid = document.getElementById("papersGrid");
  if (!grid) return;

  grid.innerHTML = "";
  let papers = data.papers;

  const searchTerm = document.getElementById("searchInput")?.value?.toLowerCase() || "";
  if (searchTerm) {
    papers = papers.filter(p => p.title.toLowerCase().includes(searchTerm));
  }

  const sortValue = document.getElementById("sortSelect")?.value;
  if (sortValue === "title") {
    papers.sort((a, b) => a.title.localeCompare(b.title));
  } else {
    papers.sort((a, b) => new Date(b.published_date) - new Date(a.published_date));
  }

  papers.forEach(p => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${p.title}</h3>
      <p><strong>Authors:</strong> ${p.authors || "Unknown"}</p>
      <p>${cleanSummary(formatSummaries(p.summary_short, "short")) || "No summary available."}</p>
      <a href="/paper/${p.id}">Read more →</a>
    `;
    grid.appendChild(card);
  });

  const pagination = document.getElementById("pagination");
  if (pagination) {
    pagination.innerHTML = "";
    for (let i = 1; i <= data.total_pages; i++) {
      const btn = document.createElement("button");
      btn.textContent = i;
      btn.disabled = (i === data.page);
      btn.addEventListener("click", () => loadPapers(i));
      pagination.appendChild(btn);
    }
  }
}

// ------------------
// Paper Detail
// ------------------
async function loadPaperDetail() {
  const match = window.location.pathname.match(/\/paper\/(\d+)/);
  if (!match) return;
  const paperId = match[1];

  const res = await fetch(`${API_BASE}/papers/${paperId}`);
  const data = await res.json();

  document.getElementById("paperTitle").textContent = data.paper.title;
  document.getElementById("paperLink").href = data.paper.pdf_url;
  document.getElementById("longSummary").innerHTML =
    cleanSummary(formatSummaries(data.summaries.long, "long")) || "No long summary available.";

  const factsList = document.getElementById("factsList");
  factsList.innerHTML = "";
  data.facts.forEach(f => {
    const li = document.createElement("li");
    li.textContent = `${f.type}: ${f.value}`;
    factsList.appendChild(li);
  });

  const entitiesDiv = document.getElementById("entitiesList");
  entitiesDiv.innerHTML = "";
  data.entities.forEach(e => {
    const tag = document.createElement("span");
    tag.className = `entity-tag entity-${e.type}`;
    tag.textContent = `${e.entity} (${e.type})`;
    entitiesDiv.appendChild(tag);
  });

  renderMindmap(data.mindmap_json);
}

// ------------------
// Mindmap Renderer
// ------------------
function renderMindmap(mindmapJson) {
  if (!mindmapJson) return;

  let data;
  try {
    data = typeof mindmapJson === "string" ? JSON.parse(mindmapJson) : mindmapJson;
  } catch (e) {
    console.error("Invalid mindmap JSON", e);
    return;
  }

  if (data.nodes && data.edges) {
    data = convertToHierarchy(data);
  }

  const container = document.getElementById("mindmapContainer");
  if (!container) return;
  container.innerHTML = "";

  const width = container.clientWidth || 800;
  const height = container.clientHeight || 600;

  const svg = d3.select(container)
    .append("svg")
    .attr("width", width)
    .attr("height", height)
    .call(d3.zoom().on("zoom", (event) => {
      g.attr("transform", event.transform);
    }));

  const g = svg.append("g").attr("transform", "translate(50,50)");

  const root = d3.hierarchy(data);
  const treeLayout = d3.tree().size([height - 100, width - 200]);
  treeLayout(root);

  // Collapse all children initially
  root.children?.forEach(collapse);

  update(root);

  function collapse(d) {
    if (d.children) {
      d._children = d.children;
      d._children.forEach(collapse);
      d.children = null;
    }
  }

  function update(source) {
    const nodes = root.descendants();
    const links = root.links();

    treeLayout(root);

    // Links
    const link = g.selectAll("path.link")
      .data(links, d => d.target.id);

    link.enter()
      .append("path")
      .attr("class", "link")
      .attr("d", d3.linkHorizontal()
        .x(d => d.y)
        .y(d => d.x))
      .attr("fill", "none")
      .attr("stroke", "#aaa");

    // Nodes
    const node = g.selectAll("g.node")
      .data(nodes, d => d.id || (d.id = ++i));

    const nodeEnter = node.enter()
      .append("g")
      .attr("class", "node")
      .attr("transform", d => `translate(${source.y0 || source.y},${source.x0 || source.x})`)
      .on("click", (event, d) => {
        if (d.children) {
          d._children = d.children;
          d.children = null;
        } else {
          d.children = d._children;
          d._children = null;
        }
        update(d);
      });

    nodeEnter.append("circle")
      .attr("r", 8)
      .attr("fill", d => d._children ? "#ff7f50" : "#2575fc")
      .style("cursor", "pointer");

    nodeEnter.append("text")
      .attr("dy", 3)
      .attr("x", d => d.children || d._children ? -12 : 12)
      .style("text-anchor", d => d.children || d._children ? "end" : "start")
      .text(d => d.data.name || d.data.label);

    nodeEnter.merge(node)
      .transition()
      .duration(300)
      .attr("transform", d => `translate(${d.y},${d.x})`);

    node.exit().remove();

    // Save old positions
    nodes.forEach(d => {
      d.x0 = d.x;
      d.y0 = d.y;
    });
  }
}


// ------------------
// Init
// ------------------
document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("papersGrid")) {
    loadPapers();
    document.getElementById("searchInput").addEventListener("input", () => loadPapers());
    document.getElementById("sortSelect").addEventListener("change", () => loadPapers());
  }

  if (document.getElementById("paperTitle")) {
    loadPaperDetail();
  }
});


// Dark Mode Toggle
document.addEventListener("DOMContentLoaded", () => {
  const toggleBtn = document.getElementById("darkModeToggle");
  if (toggleBtn) {
    // Load saved preference
    if (localStorage.getItem("dark-mode") === "true") {
      document.body.classList.add("dark-mode");
      toggleBtn.textContent = "☀️ Light Mode";
    }

    toggleBtn.addEventListener("click", () => {
      document.body.classList.toggle("dark-mode");
      const isDark = document.body.classList.contains("dark-mode");
      toggleBtn.textContent = isDark ? "☀️ Light Mode" : "🌙 Dark Mode";
      localStorage.setItem("dark-mode", isDark);
    });
  }
});

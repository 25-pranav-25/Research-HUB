from flask import Flask, jsonify, request, send_from_directory, abort
from db_utils import list_papers, get_full_paper
import os
import subprocess


app = Flask(__name__, static_folder=".", template_folder=".")

# ------------------------
# Frontend Routes
# ------------------------

@app.route("/")
def home_page():
    """Serve the main homepage (index.html)."""
    return send_from_directory(".", "index.html")

@app.route("/paper/<int:paper_id>")
def paper_page(paper_id):
    """Serve the paper details page (paper.html)."""
    # paper.html will use JS to fetch details from /api/papers/<id>
    return send_from_directory(".", "paper.html")

@app.route("/<path:filename>")
def static_files(filename):
    """
    Serve static assets like styles.css, script.js, or images.
    Only files in project-root are allowed.
    """
    if os.path.exists(filename):
        return send_from_directory(".", filename)
    else:
        abort(404)

# ------------------------
# API Routes
# ------------------------

@app.route("/api/papers", methods=["GET"])
def api_get_papers():
    """Return paginated list of papers with short summaries."""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
    except ValueError:
        page, per_page = 1, 10

    papers, total_papers = list_papers(page, per_page)
    total_pages = (total_papers + per_page - 1) // per_page  # ceil division

    return jsonify({
        "page": page,
        "per_page": per_page,
        "total_papers": total_papers,
        "total_pages": total_pages,
        "papers": papers
    })

@app.route("/api/papers/<int:paper_id>", methods=["GET"])
def api_paper_detail(paper_id):
    """Return full details of a single paper."""
    data = get_full_paper(paper_id)
    if not data:
        return jsonify({"error": "Paper not found"}), 404
    return jsonify(data)

 ------------------------
# New Route: Trigger main.py --fetch
# ------------------------

@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    """Trigger main.py with --fetch flag."""
    try:
        result = subprocess.run(
            ["python", "main.py", "--fetch"],
            capture_output=True,
            text=True,
            check=True
        )
        return jsonify({
            "status": "success",
            "output": result.stdout
        })
    except subprocess.CalledProcessError as e:
        return jsonify({
            "status": "error",
            "error": e.stderr
        }), 500

# ------------------------
# Main
# ------------------------

if __name__ == "__main__":
    app.run(debug=True)

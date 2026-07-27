import os
import requests
from flask import Flask, render_template_string, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# --- Konfiguration ---
OPENHAB_URL = os.getenv("OPENHAB_URL", "http://127.0.0.1:8080")
OPENHAB_TOKEN = os.getenv("OPENHAB_TOKEN", "")


def fetch_raw_items_from_api():
    """Holt die Items inklusive Semantik-Metadaten direkt von openHAB."""
    url = f"{OPENHAB_URL}/rest/items?recursive=false&metadata=semantics"
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {OPENHAB_TOKEN}" if OPENHAB_TOKEN else ""
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Fehler bei der Kommunikation mit openHAB: {e}")
        return None


def build_semantic_tree():
    """Baut den semantischen Baum direkt aus den live abgerufenen Daten auf."""
    raw_items = fetch_raw_items_from_api()
    if raw_items is None:
        return {"error": "Verbindung zu openHAB fehlgeschlagen. Bitte URL und Token prüfen."}

    items_dict = {item["name"]: item for item in raw_items}
    nodes = {}

    # 1. Alle semantischen Knoten identifizieren
    for name, item in items_dict.items():
        semantics = item.get("metadata", {}).get("semantics", {})
        sem_value = semantics.get("value")

        if not sem_value:
            continue  # Nicht-semantische Items ignorieren

        sem_type = sem_value.split("_")[0]  # "Location", "Equipment" oder "Point"

        nodes[name] = {
            "name": name,
            "label": item.get("label", name),
            "type": item.get("type", "Group"),
            "category": item.get("category", ""),
            "state": item.get("state", "NULL"),
            "semantic_type": sem_type,
            "semantic_value": sem_value,
            "children": []
        }

    # 2. Hierarchische Verknüpfung herstellen
    root_nodes = []

    for name, node in nodes.items():
        item = items_dict[name]
        semantics_config = item.get("metadata", {}).get("semantics", {}).get("config", {})

        # Ziel-Elternknoten bestimmen (isPartOf, isPointOf, hasLocation)
        parent_name = (
            semantics_config.get("isPartOf") or
            semantics_config.get("isPointOf") or
            semantics_config.get("hasLocation")
        )

        if parent_name and parent_name in nodes:
            nodes[parent_name]["children"].append(node)
        else:
            # Fallback über die normalen groupNames
            found_parent = False
            for grp in item.get("groupNames", []):
                if grp in nodes:
                    nodes[grp]["children"].append(node)
                    found_parent = True
                    break

            if not found_parent:
                root_nodes.append(node)

    # Nur Locations auf oberster Ebene zurückgeben
    locations = [node for node in root_nodes if node["semantic_type"] == "Location"]
    return locations if locations else root_nodes


# --- API Routes ---

@app.route("/api/tree")
def api_tree():
    """REST API Route, die den live berechneten Baum ausgibt."""
    tree = build_semantic_tree()
    if isinstance(tree, dict) and "error" in tree:
        return jsonify(tree), 500
    return jsonify(tree)


# --- Web-Interface ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>openHAB Live-Semantik-Baum</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
    <style>
        body { background-color: #f4f6f9; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .tree-card { border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
        
        /* Tree Styling */
        .tree, .tree ul { list-style: none; margin: 0; padding: 0; }
        .tree ul { padding-left: 24px; border-left: 2px dashed #dee2e6; margin-left: 12px; }
        .tree li { margin: 6px 0; position: relative; }
        
        .tree-node {
            display: flex;
            align-items: center;
            padding: 8px 12px;
            background: #ffffff;
            border: 1px solid #e9ecef;
            border-radius: 8px;
            transition: all 0.2s ease;
        }
        .tree-node:hover { background: #f8f9fa; border-color: #ced4da; box-shadow: 0 2px 6px rgba(0,0,0,0.04); }
        
        /* Semantik Badges */
        .badge-Location { background-color: #0d6efd; color: white; }
        .badge-Equipment { background-color: #198754; color: white; }
        .badge-Point { background-color: #ffc107; color: #212529; }

        .toggle-icon { cursor: pointer; margin-right: 8px; font-size: 1.1rem; width: 20px; text-align: center; }
        .item-name { font-size: 0.85rem; color: #6c757d; margin-left: 6px; }
        .item-state { font-size: 0.85rem; font-weight: 600; color: #495057; margin-left: auto; }
        .node-details { font-size: 0.75rem; color: #adb5bd; margin-left: 8px; }
    </style>
</head>
<body>

<div class="container py-5">
    <div class="row justify-content-center">
        <div class="col-lg-10">
            
            <div class="d-flex justify-content-between align-items-center mb-4">
                <div>
                    <h2 class="fw-bold mb-1">openHAB Live-Semantik-Baum</h2>
                    <p class="text-muted mb-0">Direkte Abfrage über REST API von <code>{{ openhab_url }}</code></p>
                </div>
                <button class="btn btn-outline-primary" onclick="loadTree()"><i class="bi bi-arrow-clockwise"></i> Live Aktualisieren</button>
            </div>

            <!-- Suchfeld & Quick-Actions -->
            <div class="card tree-card mb-4">
                <div class="card-body d-flex gap-3">
                    <input type="text" id="searchInput" class="form-control" placeholder="In Räumen, Geräten oder Points suchen..." onkeyup="filterTree()">
                    <button class="btn btn-light border text-nowrap" onclick="toggleAll(true)">Alle ausklappen</button>
                    <button class="btn btn-light border text-nowrap" onclick="toggleAll(false)">Alle einklappen</button>
                </div>
            </div>

            <!-- Der Baum -->
            <div class="card tree-card">
                <div class="card-body p-4">
                    <div id="treeContainer">
                        <div class="text-center py-5">
                            <div class="spinner-border text-primary" role="status"></div>
                            <p class="mt-2 text-muted">Lade Daten von openHAB REST-API...</p>
                        </div>
                    </div>
                </div>
            </div>

        </div>
    </div>
</div>

<script>
    let treeData = [];

    function loadTree() {
        document.getElementById('treeContainer').innerHTML = `
            <div class="text-center py-5">
                <div class="spinner-border text-primary" role="status"></div>
                <p class="mt-2 text-muted">Frage openHAB REST API ab...</p>
            </div>`;

        fetch('/api/tree')
            .then(res => {
                if(!res.ok) throw new Error("HTTP Fehler " + res.status);
                return res.json();
            })
            .then(data => {
                if (data.error) {
                    throw new Error(data.error);
                }
                treeData = data;
                renderTree();
            })
            .catch(err => {
                document.getElementById('treeContainer').innerHTML = `
                    <div class="alert alert-danger mb-0">
                        <h5 class="alert-heading"><i class="bi bi-exclamation-triangle-fill"></i> Fehler</h5>
                        ${err.message}
                    </div>`;
            });
    }

    function renderTree() {
        const container = document.getElementById('treeContainer');
        if (treeData.length === 0) {
            container.innerHTML = '<div class="alert alert-warning mb-0">Keine semantischen Knoten gefunden.</div>';
            return;
        }

        let html = '<ul class="tree">';
        for (let root of treeData) {
            html += createNodeHtml(root);
        }
        html += '</ul>';
        container.innerHTML = html;
    }

    function createNodeHtml(node) {
        const hasChildren = node.children && node.children.length > 0;
        
        let iconClass = "bi-tag";
        if (node.semantic_type === "Location") iconClass = "bi-house-door-fill text-primary";
        if (node.semantic_type === "Equipment") iconClass = "bi-cpu-fill text-success";
        if (node.semantic_type === "Point") iconClass = "bi-circle-fill text-warning";

        let html = `<li>`;
        html += `<div class="tree-node">`;
        
        if (hasChildren) {
            html += `<span class="toggle-icon bi bi-chevron-down" onclick="toggleNode(this)"></span>`;
        } else {
            html += `<span class="toggle-icon bi bi-dot text-muted"></span>`;
        }

        html += `<i class="${iconClass} me-2"></i>`;
        html += `<strong class="me-2">${escapeHtml(node.label)}</strong>`;
        html += `<span class="badge badge-${node.semantic_type}">${node.semantic_type}</span>`;
        html += `<span class="node-details">(${escapeHtml(node.semantic_value)})</span>`;
        html += `<span class="item-name">(${escapeHtml(node.name)})</span>`;

        if (node.state && node.state !== "NULL" && node.state !== "UNDEF") {
            html += `<span class="item-state badge bg-light text-dark border">${escapeHtml(node.state)}</span>`;
        }

        html += `</div>`;

        if (hasChildren) {
            html += `<ul>`;
            for (let child of node.children) {
                html += createNodeHtml(child);
            }
            html += `</ul>`;
        }

        html += `</li>`;
        return html;
    }

    function toggleNode(element) {
        const parentLi = element.closest('li');
        const childUl = parentLi.querySelector('ul');
        if (childUl) {
            if (childUl.style.display === "none") {
                childUl.style.display = "block";
                element.classList.replace('bi-chevron-right', 'bi-chevron-down');
            } else {
                childUl.style.display = "none";
                element.classList.replace('bi-chevron-down', 'bi-chevron-right');
            }
        }
    }

    function toggleAll(expand) {
        document.querySelectorAll('.tree ul').forEach(ul => {
            ul.style.display = expand ? 'block' : 'none';
        });
        document.querySelectorAll('.toggle-icon.bi-chevron-right, .toggle-icon.bi-chevron-down').forEach(icon => {
            if (expand) {
                icon.classList.replace('bi-chevron-right', 'bi-chevron-down');
            } else {
                icon.classList.replace('bi-chevron-down', 'bi-chevron-right');
            }
        });
    }

    function filterTree() {
        const query = document.getElementById('searchInput').value.toLowerCase();
        const allNodes = document.querySelectorAll('.tree li');

        if (!query) {
            allNodes.forEach(li => li.style.display = '');
            return;
        }

        allNodes.forEach(li => {
            const text = li.textContent.toLowerCase();
            if (text.includes(query)) {
                li.style.display = '';
                let parent = li.parentElement;
                while (parent) {
                    if (parent.tagName === 'UL') parent.style.display = 'block';
                    if (parent.tagName === 'LI') parent.style.display = '';
                    parent = parent.parentElement;
                }
            } else {
                li.style.display = 'none';
            }
        });
    }

    function escapeHtml(str) {
        return str ? str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") : '';
    }

    loadTree();
</script>

</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE, openhab_url=OPENHAB_URL)


if __name__ == "__main__":
    print(f"Starte Live-Semantik-Viewer unter http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)

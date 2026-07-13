import os
from flask import Flask, render_template_string, jsonify
import requests

app = Flask(__name__)

# --- KONFIGURATION ---
OPENHAB_URL = "http://localhost:8080"  # Ihre openHAB-IP
API_TOKEN = ""


def get_openhab_items():
    headers = {}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    headers["Accept"] = "application/json"

    try:
        response = requests.get(
            f"{OPENHAB_URL}/rest/items?fields=name,label,type,groupNames,tags",
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Fehler beim Abrufen der openHAB REST API: {e}")
        return []


def build_semantic_tree(items):
    tree = {}

    # 1. Schritt: Nur Items verarbeiten, die überhaupt mindestens ein Tag besitzen!
    semantic_items = [item for item in items if item.get("tags")]
    semantic_names = {item["name"] for item in semantic_items}

    for item in semantic_items:
        name = item["name"]
        tags = item.get("tags", [])

        # Bestimme den semantischen Typ anhand der Tags
        semantic_type = "None"
        
        # Prüfe auf Point-Strukturen (enthalten oft einen Doppelpunkt wie Point:Control)
        if any(":" in tag for tag in tags):
            semantic_type = "Point"

        for tag in tags:
            # Standard-openHAB-Tags für Typen abgleichen
            if tag.lower() == "location":
                semantic_type = "Location"
            elif tag.lower() == "equipment":
                semantic_type = "Equipment"
            elif tag.lower() == "point":
                semantic_type = "Point"
            
            # Abgleich gegen bekannte Location-Standard-Tags
            if tag in [
                "Indoor", "Outdoor", "Building", "Floor", "Room", 
                "LivingRoom", "Kitchen", "Bedroom", "Bathroom", "Corridor", "Office"
            ]:
                semantic_type = "Location"

        # Wenn keine genaue Zuordnung erfolgte, Heuristik anhand des Item-Typs
        if semantic_type == "None":
            if item["type"] == "Group":
                # Wenn es eine Gruppe ist und Location-Tags fehlen, ist es meist Equipment
                semantic_type = "Equipment"
            else:
                semantic_type = "Point"

        tree[name] = {
            "name": name,
            "label": item.get("label") or name,
            "type": item["type"],
            "semantic_type": semantic_type,
            "tags": tags,
            "children": [],
        }

    root_nodes = []

    # 2. Schritt: Beziehungen aufbauen
    for item in semantic_items:
        name = item["name"]
        group_names = item.get("groupNames", [])

        # Filter: Nur Gruppen-Zugehörigkeiten beachten, die selbst semantische Tags haben
        valid_groups = [g for g in group_names if g in semantic_names]

        if not valid_groups:
            # Keine gültige übergeordnete semantische Gruppe -> Wurzelknoten
            root_nodes.append(tree[name])
        else:
            orphan = True
            for group_name in valid_groups:
                if group_name in tree:
                    tree[group_name]["children"].append(tree[name])
                    orphan = False
            if orphan:
                root_nodes.append(tree[name])

    # Filtern der Wurzeln: Auf oberster Ebene wollen wir nur echte Locations sehen
    final_tree = [
        node
        for node in root_nodes
        if node["semantic_type"] == "Location"
    ]

    return final_tree


# --- HTML/JS FRONTEND (Inlined für einfache Ausführung) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>openHAB Semantic Model Viewer</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #f4f6f9;
            margin: 0;
            padding: 20px;
            color: #333;
        }
        h1 {
            color: #1c2d42;
            border-bottom: 2px solid #34495e;
            padding-bottom: 10px;
        }
        .tree-container {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.05);
            max-width: 800px;
            margin: 0 auto;
        }
        ul {
            list-style-type: none;
            padding-left: 20px;
        }
        li {
            margin: 5px 0;
            position: relative;
            line-height: 24px;
        }
        .caret {
            cursor: pointer;
            user-select: none;
            font-weight: bold;
            color: #2980b9;
        }
        .caret::before {
            content: "▶ ";
            display: inline-block;
            margin-right: 6px;
            transition: transform 0.2s;
        }
        .caret-down::before {
            transform: rotate(90deg);
        }
        .nested {
            display: none;
        }
        .active {
            display: block;
        }
        .badge {
            font-size: 0.75rem;
            padding: 2px 6px;
            border-radius: 4px;
            margin-left: 10px;
            font-weight: bold;
            text-transform: uppercase;
        }
        .Location { background-color: #2ecc71; color: white; }
        .Equipment { background-color: #f1c40f; color: #333; }
        .Point { background-color: #3498db; color: white; }
        .item-name {
            font-size: 0.85rem;
            color: #7f8c8d;
            margin-left: 5px;
        }
    </style>
</head>
<body>

<div class="tree-container">
    <h1>openHAB Semantic Model (Gefiltert)</h1>
    <div id="tree">Lade Daten aus openHAB...</div>
</div>

<script>
    fetch('/api/tree')
        .then(response => response.json())
        .then(data => {
            const treeContainer = document.getElementById('tree');
            treeContainer.innerHTML = '';
            
            if(data.length === 0) {
                treeContainer.innerHTML = "<p>Keine semantischen Daten gefunden. Bitte prüfen Sie Ihre openHAB-Verbindung oder Tags.</p>";
                return;
            }

            function renderNode(node) {
                const li = document.createElement('li');
                
                const spanNode = document.createElement('span');
                spanNode.textContent = node.label;
                li.appendChild(spanNode);

                const spanName = document.createElement('span');
                spanName.className = 'item-name';
                spanName.textContent = `(${node.name})`;
                li.appendChild(spanName);

                const badge = document.createElement('span');
                badge.className = `badge ${node.semantic_type}`;
                badge.textContent = node.semantic_type;
                li.appendChild(badge);

                if (node.children && node.children.length > 0) {
                    spanNode.className = 'caret';
                    const ul = document.createElement('ul');
                    ul.className = 'nested';
                    
                    node.children.forEach(child => {
                        ul.appendChild(renderNode(child));
                    });
                    
                    li.appendChild(ul);

                    spanNode.addEventListener('click', function() {
                        this.parentElement.querySelector('.nested').classList.toggle('active');
                        this.classList.toggle('caret-down');
                    });
                }
                
                return li;
            }

            const rootUl = document.createElement('ul');
            data.forEach(rootNode => {
                rootUl.appendChild(renderNode(rootNode));
            });
            treeContainer.appendChild(rootUl);
        })
        .catch(err => {
            document.getElementById('tree').innerHTML = "<p style='color:red;'>Fehler beim Laden der Daten: " + err + "</p>";
        });
</script>

</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/tree")
def api_tree():
    raw_items = get_openhab_items()
    semantic_tree = build_semantic_tree(raw_items)
    return jsonify(semantic_tree)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
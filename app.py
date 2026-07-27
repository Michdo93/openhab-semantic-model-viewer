import os
from flask import Flask, render_template_string, jsonify
from dotenv import load_dotenv

# python-openhab-rest-client (dieselbe Library wie in der oh-ai-bridge)
from openhab import OpenHABClient, Items, Tags

load_dotenv()

app = Flask(__name__)

# --- KONFIGURATION (.env statt Klartext-Credentials im Code) ---
OPENHAB_URL = os.getenv("OPENHAB_URL", "http://127.0.0.1:8080")
OPENHAB_TOKEN = os.getenv("OPENHAB_TOKEN", "")

client = OpenHABClient(url=OPENHAB_URL, token=OPENHAB_TOKEN or None)
items_api = Items(client)
tags_api = Tags(client)

# =====================================================================
# TAG-REGISTRY: klassifiziert jeden Tag-Namen als Location/Equipment/
# Point/Property über die echte openHAB-Hierarchie (/rest/tags).
#
# WICHTIG (Fix ggü. Vorversion): Wir wissen nicht mit Sicherheit, ob
# /rest/tags qualifizierte UIDs wie "Location_Kitchen" oder kurze Namen
# wie "Kitchen" liefert -- und ob deine Items dieselbe Schreibweise auf
# ihren "tags" tragen. Bisher haben wir das einfach angenommen, und genau
# das war vermutlich der Grund für den leeren Baum. Jetzt wird JEDER Tag
# unter BEIDEM registriert (voller Name + kurzer Name nach dem letzten
# "_"), und beim Klassifizieren eines Item-Tags wird zuerst exakt, dann
# über den Kurznamen gesucht -- funktioniert unabhängig davon, welche der
# beiden Schreibweisen dein System tatsächlich benutzt.
# =====================================================================
_tag_parent = {}
_short_to_uid = {}
_raw_tags_sample = []
ROOT_CATEGORIES = {"Location", "Equipment", "Point", "Property"}


def load_tag_registry():
    global _tag_parent, _short_to_uid, _raw_tags_sample
    _tag_parent = {}
    _short_to_uid = {}
    try:
        tags = tags_api.getTags(language="de")
        if not isinstance(tags, list):
            print(f"Warnung: Tags.getTags() lieferte kein verwertbares Ergebnis: {tags!r}")
            return
        # Bewusst nicht einfach tags[:5] -- ein Root-Tag allein (ohne
        # Parent) verrät nichts über das Feldformat von Kindern. Wir
        # nehmen zusätzlich gezielt ein paar Nicht-Root-Einträge mit.
        root_sample = [t for t in tags if (t.get("uid") or t.get("name")) in ROOT_CATEGORIES][:2]
        non_root_sample = [t for t in tags if (t.get("uid") or t.get("name")) not in ROOT_CATEGORIES][:5]
        _raw_tags_sample = root_sample + non_root_sample
        for t in tags:
            uid = t.get("uid") or t.get("name") or t.get("id")
            if not uid:
                continue
            parent = t.get("parentTag") or t.get("parent")
            _tag_parent[uid] = parent
            short = uid.split("_")[-1] if "_" in uid else uid
            _short_to_uid.setdefault(short, uid)
        print(f"[TAGS] {len(tags)} Tags geladen.")
        if tags:
            print(f"[TAGS] Beispiel-Eintrag (roh, zur Kontrolle des Feldformats): {tags[0]!r}")
    except Exception as e:
        print(f"Warnung: Tag-Registry konnte nicht geladen werden: {e}")


_category_cache = {}


def _resolve_uid(tag_name):
    """Findet den Registry-Key zu einem rohen Item-Tag: zuerst exakt,
    dann über die Kurzname-Tabelle (s. Kommentar oben)."""
    if tag_name in ROOT_CATEGORIES or tag_name in _tag_parent:
        return tag_name
    return _short_to_uid.get(tag_name)


def tag_category(tag_name):
    """Läuft die Parent-Kette eines Tags hoch bis zu einer der vier
    Wurzelkategorien. z.B. 'Bathroom'/'Location_Bathroom' -> 'Location'."""
    if tag_name in _category_cache:
        return _category_cache[tag_name]
    uid = _resolve_uid(tag_name)
    if not uid:
        _category_cache[tag_name] = None
        return None

    # Versuch 1: über das parentTag-Feld hochlaufen (falls vorhanden/gepflegt).
    seen = set()
    current = uid
    while current and current not in seen:
        if current in ROOT_CATEGORIES:
            _category_cache[tag_name] = current
            return current
        seen.add(current)
        current = _tag_parent.get(current)

    # Versuch 2 (Fallback): dein System liefert offenbar KEIN befülltes
    # parentTag-Feld (siehe /api/debug: das Root-Tag 'Equipment' hat den
    # Key gar nicht erst). openHAB kodiert die Hierarchie stattdessen direkt
    # im UID-String, z.B. "Location_Indoor_Room_Bathroom" -- das erste
    # Segment ist dann unmittelbar die Wurzelkategorie.
    first_segment = uid.split("_")[0]
    result = first_segment if first_segment in ROOT_CATEGORIES else None
    _category_cache[tag_name] = result
    return result


def classify_item(tags):
    """Ordnet ein Item über seine ECHTEN Tags einer Kategorie zu.
    Gibt (semantic_type, property_name) zurück -- property_name nur bei
    Point gesetzt, wenn zusätzlich ein Property-Tag (z.B. 'Light') vorhanden
    ist. Kein Tag klassifizierbar -> (None, None), Item taucht im Baum
    dann nicht auf (kein Rate-Fallback mehr auf Name/Label!)."""
    point_found = False
    property_name = None
    for t in tags:
        cat = tag_category(t)
        if cat == "Location":
            return "Location", None
        if cat == "Equipment":
            return "Equipment", None
        if cat == "Point":
            point_found = True
        elif cat == "Property":
            property_name = t
    if point_found or property_name:
        return "Point", property_name
    return None, None


def get_openhab_items():
    try:
        items = items_api.getItems(fields="name,label,type,groupNames,tags")
        if isinstance(items, dict) and "error" in items:
            print(f"Fehler beim Abrufen der openHAB REST API: {items['error']}")
            return []
        if not isinstance(items, list):
            print(f"Unerwartetes Antwortformat von Items.getItems(): {items!r}")
            return []
        return items
    except Exception as e:
        print(f"Fehler beim Abrufen der openHAB REST API: {e}")
        return []


def build_semantic_tree(items):
    items_by_name = {i["name"]: i for i in items}
    tree = {}

    for item in items:
        semantic_type, property_name = classify_item(item.get("tags", []))
        if semantic_type is None:
            continue
        tree[item["name"]] = {
            "name": item["name"],
            "label": item.get("label") or item["name"],
            "type": item["type"],
            "semantic_type": semantic_type,
            "property": property_name,
            "tags": item.get("tags", []),
            "children": [],
        }

    def nearest_semantic_ancestor(item):
        """Läuft die groupNames-Kette hoch (BFS), bis eine Gruppe gefunden
        wird, die SELBST semantisch getaggt ist. Überspringt dabei rein
        organisatorische Zwischen-Gruppen ohne eigenes Tag."""
        visited = set()
        frontier = list(item.get("groupNames", []))
        while frontier:
            gname = frontier.pop(0)
            if gname in visited:
                continue
            visited.add(gname)
            if gname in tree:
                return gname
            parent_item = items_by_name.get(gname)
            if parent_item:
                frontier.extend(parent_item.get("groupNames", []))
        return None

    root_nodes = []
    for item in items:
        name = item["name"]
        if name not in tree:
            continue
        ancestor_name = nearest_semantic_ancestor(item)
        if ancestor_name and ancestor_name in tree:
            tree[ancestor_name]["children"].append(tree[name])
        else:
            root_nodes.append(tree[name])

    return [node for node in root_nodes if node["semantic_type"] == "Location"]


# --- HTML/JS FRONTEND ---
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
        .property-tag {
            font-size: 0.75rem;
            color: #8e44ad;
            margin-left: 6px;
        }
        .item-name {
            font-size: 0.85rem;
            color: #7f8c8d;
            margin-left: 5px;
        }
        .debug-link {
            display: block;
            margin-top: 15px;
            font-size: 0.85rem;
        }
    </style>
</head>
<body>

<div class="tree-container">
    <h1>openHAB Semantic Model</h1>
    <div id="tree">Lade Daten aus openHAB...</div>
    <a class="debug-link" href="/api/debug" target="_blank">→ /api/debug (Rohdaten zur Fehlersuche)</a>
</div>

<script>
    fetch('/api/tree')
        .then(response => response.json())
        .then(data => {
            const treeContainer = document.getElementById('tree');
            treeContainer.innerHTML = '';

            if(data.length === 0) {
                treeContainer.innerHTML = "<p>Keine semantischen Daten gefunden. Siehe /api/debug für Details.</p>";
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

                if (node.property) {
                    const prop = document.createElement('span');
                    prop.className = 'property-tag';
                    prop.textContent = `(${node.property})`;
                    li.appendChild(prop);
                }

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


@app.route("/api/reload_tags")
def reload_tags():
    load_tag_registry()
    return jsonify({"status": "ok", "tags_loaded": len(_tag_parent)})


@app.route("/api/debug")
def debug():
    """Zeigt genau das, was wir zur Fehlersuche brauchen, statt weiter zu
    raten: wie /rest/tags wirklich aussieht, und wie ein paar echte Items
    damit klassifiziert werden (oder eben nicht)."""
    raw_items = get_openhab_items()
    sample = []
    for item in raw_items:
        tags = item.get("tags", [])
        if not tags:
            continue
        semantic_type, property_name = classify_item(tags)
        sample.append({
            "name": item.get("name"),
            "type": item.get("type"),
            "raw_tags": tags,
            "classified_as": semantic_type,
            "property": property_name,
        })
        if len(sample) >= 20:
            break

    return jsonify({
        "tag_registry_entries": len(_tag_parent),
        "raw_tags_sample": _raw_tags_sample,
        "items_total": len(raw_items),
        "items_with_tags_sample": sample,
    })


if __name__ == "__main__":
    load_tag_registry()
    app.run(host="0.0.0.0", port=5000, debug=True)
import json
import re
import statistics

import requests

BASE = "https://www.vinted.fr"
DEFECTS = ["trou", "tache", "tâche", "taché", "déchiré", "dechire", "défaut", "defaut",
           "usé", "use ", "bouloch", "décolor", "decolor", "abîmé", "abime", "cassé", "casse"]
# Vinted FR : 6 = neuf avec étiquette, 1 = neuf sans étiquette, 2 = très bon état, 3 = bon état
STATUS_IDS = [6, 1, 2, 3]


def price_of(item):
    p = item.get("price")
    if isinstance(p, dict):
        return float(p.get("amount", 0))
    try:
        return float(p)
    except (TypeError, ValueError):
        return 0.0


def extract_items(html):
    """Extrait les articles depuis le HTML de la page catalogue (payload Next.js)."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    parts = []
    for c in chunks:
        try:
            parts.append(json.loads('"' + c + '"'))
        except ValueError:
            pass
    text = "".join(parts) if parts else html
    dec = json.JSONDecoder()
    found, seen = [], set()
    for m in re.finditer(r'"brand_title"', text):
        depth, start = 0, None
        for i in range(m.start(), max(m.start() - 6000, -1), -1):
            ch = text[i]
            if ch == "}":
                depth += 1
            elif ch == "{":
                if depth == 0:
                    start = i
                    break
                depth -= 1
        if start is None:
            continue
        try:
            obj, _ = dec.raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "id" in obj and "title" in obj and obj["id"] not in seen:
            seen.add(obj["id"])
            found.append(obj)
    return found


def _txt(v):
    if isinstance(v, dict):
        return str(v.get("title") or v.get("name") or v.get("label") or "")
    return "" if v is None else str(v)


def normalize(d):
    price = d.get("price")
    if price is None:
        price = d.get("total_item_price") or d.get("price_numeric")
    url = d.get("url") or d.get("path") or f"/items/{d.get('id')}"
    if url.startswith("/"):
        url = BASE + url
    return {
        "id": d.get("id"),
        "title": _txt(d.get("title") or d.get("name")),
        "brand_title": _txt(d.get("brand_title") or d.get("brand")),
        "size_title": _txt(d.get("size_title") or d.get("size")),
        "status": _txt(d.get("status") or d.get("condition")),
        "price": price,
        "favourite_count": d.get("favourite_count", d.get("favorite_count", 0)),
        "url": url,
    }


def extract_json(text):
    """Cherche récursivement les articles dans une réponse JSON quelle que soit sa forme."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    found, seen = [], set()

    def walk(x):
        if isinstance(x, dict):
            has_price = any(k in x for k in ("price", "total_item_price", "price_numeric"))
            if "id" in x and ("title" in x or "name" in x) and has_price:
                if x["id"] not in seen:
                    seen.add(x["id"])
                    found.append(normalize(x))
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    return found


def fetch_items(query, price_max, pages, log):
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
    except ImportError:
        s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    s.get(BASE, timeout=20)  # cookies de session
    items = []
    for page in range(1, pages + 1):
        params = [("search_text", query), ("page", page), ("order", "newest_first")]
        if price_max:
            params.append(("price_to", price_max))
        for sid in STATUS_IDS:
            params.append(("status_ids[]", sid))
        batch = []
        # Essai 1 : page catalogue (HTML)
        r = s.get(f"{BASE}/catalog", params=params, timeout=25)
        if r.status_code == 200:
            batch = extract_items(r.text)
        else:
            log(f"Catalogue HTML : HTTP {r.status_code}")
        # Essai 2 : passerelle JSON (autre route utilisée par le site)
        if not batch:
            p2 = [("search_text", query), ("page", page), ("per_page", 96),
                  ("order", "newest_first")]
            if price_max:
                p2.append(("price_to", price_max))
            for host in ("https://api.vinted.fr/svc-catalogue/items",
                         f"{BASE}/svc-catalogue/items"):
                r2 = s.get(host, params=p2, timeout=25)
                if r2.status_code == 200:
                    batch = extract_json(r2.text) or extract_items(r2.text)
                    if batch:
                        break
                    log("Réponse reçue mais illisible. Début : " + r2.text[:400])
                log(f"{host.split('/')[2]} : HTTP {r2.status_code}")
        if not batch:
            log(f"Aucun article récupéré (page {page}). Vinted bloque ou a changé son format.")
            break
        items.extend(batch)
        log(f"Page {page} : {len(batch)} articles")
    return items


def analyze(items, cfg):
    """Analyse TOUS les articles : prix de revente estimé, bénéfice, classés du plus haut au plus bas."""
    groups = {}
    for it in items:
        key = (it.get("brand_title", "").lower(), it.get("size_title", "").lower())
        groups.setdefault(key, []).append((it.get("id"), price_of(it)))

    rows = []
    for it in items:
        title = (it.get("title") or "").lower()
        if any(d in title for d in DEFECTS) or "satisf" in str(it.get("status", "")).lower():
            continue  # état exigé : bon état, sans défaut
        buy = price_of(it)
        if buy <= 0:
            continue
        key = (it.get("brand_title", "").lower(), it.get("size_title", "").lower())
        others = [p for i, p in groups.get(key, []) if i != it.get("id") and p > 0] if key[0] else []
        row = {
            "Article": it.get("title", ""), "Marque": it.get("brand_title", ""),
            "Taille": it.get("size_title", ""), "État": it.get("status", ""),
            "Prix achat": round(buy, 2), "Revente estimée": None, "Bénéfice": None,
            "Marge %": None, "Favoris": it.get("favourite_count", 0),
            "Confiance": f"insuffisante ({len(others)})",
            "Lien": it.get("url") or f"{BASE}/items/{it.get('id')}",
        }
        if len(others) >= cfg["min_comps"]:
            resale = statistics.median(others) * cfg["discount"]
            cost = buy + cfg["ship"] + cfg["pack"]
            profit = resale - cost
            n = len(others)
            row.update({
                "Revente estimée": round(resale, 2), "Bénéfice": round(profit, 2),
                "Marge %": round(profit / cost * 100, 1) if cost else None,
                "Confiance": ("élevée" if n >= 10 else "moyenne" if n >= 5 else "faible") + f" ({n})",
            })
        rows.append(row)
    # tri : bénéfice du plus haut au plus bas, les articles non estimables en dernier
    rows.sort(key=lambda r: (r["Bénéfice"] is None, -(r["Bénéfice"] or 0)))
    return rows


import pandas as pd
import streamlit as st

st.set_page_config(page_title="Vinted Flip Analyzer", layout="wide")
st.title("Vinted Flip Analyzer")

query = st.text_input("Recherche", "nike air force 1")
c1, c2 = st.columns(2)
budget = c1.number_input("Budget max €", 1.0, 1000.0, 50.0)
pages = c2.number_input("Pages (96 art./page)", 1, 10, 2)
c3, c4, c5 = st.columns(3)
min_comps = c3.number_input("Comparables min", 1, 50, 3)
pack = c4.number_input("Emballage €", 0.0, 20.0, 0.5)
discount = c5.number_input("Coef. prix de vente réel", 0.5, 1.2, 0.9)
only_profit = st.checkbox("Masquer les articles non rentables (bénéfice ≤ 0) et non estimables")

if st.button("Analyser", type="primary"):
    cfg = {"ship": 0.0, "pack": pack, "min_comps": int(min_comps), "discount": discount}
    with st.spinner("Récupération et analyse des articles..."):
        try:
            items = fetch_items(query, budget, int(pages), st.write)
            rows = analyze(items, cfg)
        except Exception as e:
            st.error(f"Erreur : {e}")
            st.stop()
    if only_profit:
        rows = [r for r in rows if r["Bénéfice"] is not None and r["Bénéfice"] > 0]
    est = sum(1 for r in rows if r["Bénéfice"] is not None)
    st.success(f"{len(items)} articles récupérés, {len(rows)} analysés ({est} avec un bénéfice estimé).")
    if rows:
        df = pd.DataFrame(rows)
        cols = ["Article", "Prix achat", "Revente estimée", "Bénéfice", "Marge %",
                "Confiance", "Marque", "Taille", "État", "Favoris", "Lien"]
        df = df[cols]
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={"Lien": st.column_config.LinkColumn("Lien", display_text="Ouvrir")})
        st.download_button("Exporter CSV", df.to_csv(index=False, sep=";").encode("utf-8-sig"),
                           "vinted.csv", "text/csv")

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


def fetch_items(query, price_max, pages, log):
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    s.get(BASE, timeout=15)  # récupère les cookies de session
    items = []
    for page in range(1, pages + 1):
        params = [("search_text", query), ("page", page), ("per_page", 96),
                  ("order", "newest_first")]
        if price_max:
            params.append(("price_to", price_max))
        for sid in STATUS_IDS:
            params.append(("status_ids[]", sid))
        r = s.get(f"{BASE}/api/v2/catalog/items", params=params, timeout=20)
        if r.status_code != 200:
            log(f"Erreur HTTP {r.status_code} (Vinted bloque peut-être les requêtes). Réessaie plus tard.")
            break
        batch = r.json().get("items", [])
        if not batch:
            break
        items.extend(batch)
        log(f"Page {page} : {len(batch)} articles")
    return items


def analyze(items, cfg):
    # groupes de comparables : même marque + même taille
    groups = {}
    for it in items:
        key = (it.get("brand_title", "").lower(), it.get("size_title", "").lower())
        groups.setdefault(key, []).append(price_of(it))

    rows = []
    for it in items:
        title = (it.get("title") or "").lower()
        if any(d in title for d in DEFECTS):
            continue
        buy = price_of(it)
        if buy <= 0:
            continue
        key = (it.get("brand_title", "").lower(), it.get("size_title", "").lower())
        comps = groups[key]
        if len(comps) < cfg["min_comps"]:
            continue
        resale = statistics.median(comps) * cfg["discount"]
        cost = buy + cfg["ship"] + cfg["pack"]
        profit = resale - cost
        margin = profit / cost * 100 if cost else 0
        if profit < cfg["min_profit"] or margin < cfg["min_margin"]:
            continue
        n = len(comps)
        conf = "élevé" if n >= 10 else "moyen" if n >= 5 else "faible"
        rows.append({
            "Article": it.get("title", ""), "Marque": it.get("brand_title", ""),
            "Taille": it.get("size_title", ""), "État": it.get("status", ""),
            "Prix achat": round(buy, 2), "Revente estimée": round(resale, 2),
            "Bénéfice": round(profit, 2), "Marge %": round(margin, 1),
            "Favoris": it.get("favourite_count", 0), "Confiance": f"{conf} ({n})",
            "Lien": it.get("url") or f"{BASE}/items/{it.get('id')}",
        })
    rows.sort(key=lambda r: r["Bénéfice"], reverse=True)
    return rows



import pandas as pd
import streamlit as st

st.set_page_config(page_title="Vinted Flip Analyzer", layout="wide")
st.title("Vinted Flip Analyzer")

c1, c2, c3 = st.columns(3)
query = c1.text_input("Recherche", "nike hoodie")
budget = c2.number_input("Budget max €", 1.0, 1000.0, 20.0)
pages = c3.number_input("Pages (96 art./page)", 1, 10, 2)
c4, c5, c6 = st.columns(3)
min_profit = c4.number_input("Bénéfice min €", 0.0, 500.0, 8.0)
min_margin = c5.number_input("Marge min %", 0.0, 500.0, 40.0)
min_comps = c6.number_input("Comparables min", 1, 50, 3)
c7, c8, c9 = st.columns(3)
ship = c7.number_input("Port d'achat €", 0.0, 50.0, 3.0)
pack = c8.number_input("Emballage €", 0.0, 20.0, 0.5)
discount = c9.number_input("Coef. prix de vente réel", 0.5, 1.2, 0.9)

if st.button("Analyser", type="primary"):
    cfg = {"min_profit": min_profit, "min_margin": min_margin, "ship": ship,
           "pack": pack, "min_comps": int(min_comps), "discount": discount}
    with st.spinner("Récupération des articles..."):
        try:
            items = fetch_items(query, budget, int(pages), st.write)
            rows = analyze(items, cfg)
        except Exception as e:
            st.error(f"Erreur : {e}")
            st.stop()
    st.success(f"{len(items)} articles analysés, {len(rows)} opportunités.")
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True,
                     column_config={"Lien": st.column_config.LinkColumn("Lien")})
        st.download_button("Exporter CSV", df.to_csv(index=False, sep=";").encode("utf-8-sig"),
                           "vinted.csv", "text/csv")

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


PAYS_NOMS = {"états-unis": "US", "etats-unis": "US", "états unis": "US", "etats unis": "US",
             "united states": "US", "usa": "US", "france": "FR", "espagne": "ES", "spain": "ES", "españa": "ES", "espana": "ES",
             "portugal": "PT", "italie": "IT", "italy": "IT", "italia": "IT",
             "belgique": "BE", "belgium": "BE", "allemagne": "DE", "germany": "DE",
             "pays-bas": "NL", "netherlands": "NL", "luxembourg": "LU", "pologne": "PL",
             "poland": "PL", "autriche": "AT", "austria": "AT"}


def find_country(d):
    """Pays du vendeur (code ISO sur 2 lettres) si Vinted le fournit, sinon ''."""
    for holder in (d, d.get("user"), d.get("seller")):
        if isinstance(holder, dict):
            for k in ("country_iso_code", "country_code", "country_iso", "country"):
                v = holder.get(k)
                if isinstance(v, dict):
                    v = v.get("iso_code") or v.get("code") or v.get("title")
                if v:
                    v = str(v).strip()
                    return v.upper() if len(v) == 2 else PAYS_NOMS.get(v.lower(), v.upper())
    # pas de champ pays : cherche une mention des États-Unis dans les infos du vendeur
    for holder in (d.get("user"), d.get("seller")):
        if isinstance(holder, dict):
            blob = json.dumps(holder, ensure_ascii=False).lower()
            if any(m in blob for m in ("états-unis", "etats-unis", "états unis", "united states")):
                return "US"
    return ""


def is_available(d):
    """False si l'annonce n'a ni bouton Acheter ni Faire une offre (vendu, réservé, masqué...)."""
    for k in ("is_closed", "is_reserved", "is_hidden", "is_draft", "is_sold"):
        if d.get(k) is True:
            return False
    if d.get("transaction_permitted") is False:
        return False
    if d.get("can_buy") is False and not d.get("can_bargain"):
        return False
    return True


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
        "country": find_country(d),
        "available": is_available(d),
        "_keys": sorted(d.keys()),
        "_user": json.dumps(d.get("user") or d.get("seller") or {}, ensure_ascii=False)[:700],
        "user_id": next((h.get("id") for h in (d.get("user"), d.get("seller"))
                         if isinstance(h, dict) and h.get("id")), d.get("user_id")),
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


def make_session():
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
    except ImportError:
        s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    s.get(BASE, timeout=20)
    return s


def seller_country(session, uid):
    """Ouvre le profil du vendeur et renvoie son pays (code ISO) ou ''."""
    for url in (f"{BASE}/api/v2/users/{uid}", f"https://api.vinted.fr/api/v2/users/{uid}"):
        try:
            r = session.get(url, timeout=20)
            if r.status_code != 200:
                continue
            data = r.json()
            user = data.get("user", data) if isinstance(data, dict) else {}
            c = find_country({"user": user}) or find_country(user)
            if c:
                return c
            blob = json.dumps(user, ensure_ascii=False).lower()
            if any(m in blob for m in ("états-unis", "etats-unis", "united states")):
                return "US"
        except Exception:
            continue
    return ""


def diag_profile(session, uid):
    out = []
    for url in (f"{BASE}/api/v2/users/{uid}", f"https://api.vinted.fr/api/v2/users/{uid}"):
        try:
            r = session.get(url, timeout=20)
            out.append(f"{url} -> HTTP {r.status_code}\n{r.text[:700]}")
        except Exception as e:
            out.append(f"{url} -> erreur {e}")
    return "\n\n".join(out)


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
        key = (it.get("_query", ""), it.get("size_title", "").lower())
        groups.setdefault(key, []).append((it.get("id"), price_of(it)))

    rows = []
    for it in items:
        title = (it.get("title") or "").lower()
        if any(d in title for d in DEFECTS) or "satisf" in str(it.get("status", "")).lower():
            continue  # état exigé : bon état, sans défaut
        if not it.get("available", is_available(it)):
            continue  # pas de bouton Acheter / Faire une offre
        buy = price_of(it)
        if buy <= 0:
            continue
        key = (it.get("_query", ""), it.get("size_title", "").lower())
        others = [p for i, p in groups.get(key, []) if i != it.get("id") and p > 0]
        row = {
            "Article": it.get("title", ""), "Marque": it.get("brand_title", ""),
            "Taille": it.get("size_title", ""), "État": it.get("status", ""),
            "Produit": it.get("_query", ""),
            "Pays": it.get("country") or find_country(it),
            "_uid": it.get("user_id") or next((h.get("id") for h in (it.get("user"), it.get("seller"))
                                               if isinstance(h, dict) and h.get("id")), None),
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


import time

import pandas as pd
import streamlit as st

POPULAIRES = [
    # Sneakers
    "nike air force 1", "nike air max 90", "nike air max plus tn", "nike dunk low", "jordan 1",
    "adidas samba", "adidas gazelle", "adidas campus", "adidas spezial", "new balance 530",
    "new balance 574", "asics gel", "salomon xt-6", "converse chuck 70", "puma suede",
    "reebok classic", "dr martens 1460", "birkenstock arizona", "ugg classic", "crocs",
    # Vêtements homme / mixte
    "nike tech fleece", "nike windrunner", "nike sweat", "adidas firebird veste", "adidas survêtement",
    "carhartt veste", "carhartt wip", "dickies 874", "stussy", "champion hoodie",
    "levis 501", "levis 511", "diesel jean", "ralph lauren pull", "ralph lauren polo",
    "lacoste polo", "fred perry polo", "tommy hilfiger pull", "calvin klein", "hugo boss",
    "stone island", "cp company", "barbour veste", "burberry écharpe", "kappa survêtement",
    # Outdoor
    "the north face doudoune", "the north face nuptse", "patagonia polaire", "arcteryx",
    "columbia veste", "napapijri", "helly hansen", "salomon veste",
    # Femme / accessoires
    "lululemon legging", "sandro pull", "maje robe", "sezane", "zadig voltaire",
    "michael kors sac", "longchamp pliage", "guess sac", "adidas legging",
    # Autres produits qui se vendent bien
    "lego", "pokemon cartes", "nintendo switch jeu", "playstation 5 manette", "airpods",
]
DEFAUT = ["nike air force 1", "nike tech fleece", "adidas samba", "levis 501",
          "carhartt veste", "ralph lauren pull", "the north face doudoune", "new balance 530"]

st.set_page_config(page_title="Vinted Flip Analyzer", layout="wide")
st.title("Vinted Flip Analyzer")
st.caption("Scan automatique des produits qui se revendent le plus : classement par bénéfice.")

choix = st.multiselect("Produits à scanner (valeurs populaires en revente)", POPULAIRES,
                       default=DEFAUT)
perso = st.text_input("Ajouter une recherche perso (optionnel)", "")
c1, c2 = st.columns(2)
budget = c1.number_input("Budget max € par article", 1.0, 1000.0, 40.0)
pages = c2.number_input("Pages par produit (96 art./page)", 1, 5, 1)
c3, c4, c5 = st.columns(3)
min_comps = c3.number_input("Comparables min", 1, 50, 3)
pack = c4.number_input("Emballage €", 0.0, 20.0, 0.5)
discount = c5.number_input("Coef. prix de vente réel", 0.5, 1.2, 0.9)
min_fav = st.number_input("Favoris min (demande)", 0, 100, 0)
pays_ok = st.multiselect("Pays des vendeurs acceptés", ["FR", "ES", "PT", "IT", "BE", "DE", "NL", "LU", "PL", "AT"],
                         default=["FR", "ES", "PT", "IT"])
strict = st.checkbox("Mode strict : n'afficher que les vendeurs dont le pays est confirmé (FR, ES, PT, IT)", value=True)
show_unknown = st.checkbox("Afficher aussi les articles sans estimation de bénéfice")

if st.button("Scanner et analyser", type="primary"):
    queries = list(choix) + ([perso.strip()] if perso.strip() else [])
    if not queries:
        st.warning("Choisis au moins un produit.")
        st.stop()
    cfg = {"ship": 0.0, "pack": pack, "min_comps": int(min_comps), "discount": discount}
    all_items, seen = [], set()
    bar = st.progress(0.0)
    status = st.empty()
    for n, q in enumerate(queries, 1):
        status.write(f"Scan : {q} ({n}/{len(queries)})")
        try:
            got = fetch_items(q, budget, int(pages), lambda m: None)
        except Exception as e:
            st.write(f"{q} : erreur ({e})")
            got = []
        for it in got:
            if it["id"] not in seen:
                seen.add(it["id"])
                it["_query"] = q
                all_items.append(it)
        bar.progress(n / len(queries))
        time.sleep(1.0)
    status.empty()
    if not all_items:
        st.error("Aucun article récupéré : Vinted bloque ou a changé son format.")
        st.stop()
    rows = analyze(all_items, cfg)
    # marges négatives toujours masquées
    rows = [r for r in rows if (r["Bénéfice"] is not None and r["Bénéfice"] > 0)
            or (show_unknown and r["Bénéfice"] is None)]
    if min_fav:
        rows = [r for r in rows if (r["Favoris"] or 0) >= min_fav]
    # Ouvre le profil de chaque vendeur dont le pays est inconnu
    todo = sorted({r["_uid"] for r in rows if not r["Pays"] and r["_uid"]})[:200]
    if todo:
        status2 = st.empty()
        bar2 = st.progress(0.0)
        try:
            sess = make_session()
            cache = {}
            for n, uid in enumerate(todo, 1):
                status2.write(f"Vérification des profils vendeurs ({n}/{len(todo)})")
                cache[uid] = seller_country(sess, uid)
                bar2.progress(n / len(todo))
                time.sleep(0.4)
            for r in rows:
                if not r["Pays"] and r["_uid"] in cache:
                    r["Pays"] = cache[r["_uid"]]
            ok = sum(1 for v in cache.values() if v)
            st.info(f"Profils vendeurs : {ok} pays lus sur {len(todo)} vérifiés.")
            if ok == 0:
                st.warning("Impossible de lire le pays sur les profils vendeurs (Vinted bloque l'accès).")
        except Exception as e:
            st.warning(f"Vérification des profils impossible : {e}")
        status2.empty()
        bar2.empty()
    rows = [r for r in rows if r["Pays"] != "US"]  # États-Unis : toujours supprimés
    if strict:
        rows = [r for r in rows if r["Pays"] in pays_ok]
        if not rows:
            st.warning("Aucun article avec un pays de vendeur confirmé. Ouvre le bloc Diagnostic "
                       "ci-dessous et envoie-moi une capture.")
    elif any(r["Pays"] for r in rows):
        rows = [r for r in rows if r["Pays"] in pays_ok]
    else:
        st.warning("Vinted ne fournit pas le pays du vendeur dans ces données : le filtre par pays "
                   "n'a pas pu s'appliquer. Vérifie le pays sur l'annonce avant d'acheter.")
    with st.expander("Diagnostic (à m'envoyer en capture si le filtre pays ne marche pas)"):
        st.write(f"Articles avec pays connu : {sum(1 for i in all_items if i.get('country'))} / {len(all_items)}")
        for it in all_items[:2]:
            st.code(f"Champs article : {it.get('_keys')}\nVendeur : {it.get('_user')}")
        uid0 = next((i.get("user_id") for i in all_items if i.get("user_id")), None)
        if uid0:
            try:
                st.code(diag_profile(make_session(), uid0))
            except Exception as e:
                st.code(f"Erreur : {e}")
        else:
            st.write("Aucun identifiant vendeur trouvé dans les données.")
    est = sum(1 for r in rows if r["Bénéfice"] is not None)
    st.success(f"{len(all_items)} articles récupérés, {len(rows)} affichés ({est} avec un bénéfice estimé).")
    if rows:
        df = pd.DataFrame(rows)
        cols = ["Article", "Prix achat", "Revente estimée", "Bénéfice", "Marge %",
                "Confiance", "Pays", "Favoris", "Produit", "Taille", "État", "Marque", "Lien"]
        df = df[cols]
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={"Lien": st.column_config.LinkColumn("Lien", display_text="Ouvrir")})
        st.download_button("Exporter CSV", df.to_csv(index=False, sep=";").encode("utf-8-sig"),
                           "vinted.csv", "text/csv")

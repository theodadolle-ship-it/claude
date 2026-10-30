import csv
import statistics
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

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


class App(tk.Tk):
    COLS = ["Article", "Marque", "Taille", "État", "Prix achat", "Revente estimée",
            "Bénéfice", "Marge %", "Favoris", "Confiance"]

    def __init__(self):
        super().__init__()
        self.title("Vinted Flip Analyzer")
        self.geometry("1150x650")
        self.rows = []
        self.vars = {}

        form = ttk.Frame(self, padding=8)
        form.pack(fill="x")
        fields = [("Recherche (ex: nike hoodie)", "query", "nike hoodie"),
                  ("Budget max €", "budget", "20"),
                  ("Bénéfice min €", "min_profit", "8"),
                  ("Marge min %", "min_margin", "40"),
                  ("Port d'achat €", "ship", "3"),
                  ("Emballage €", "pack", "0.5"),
                  ("Pages (96 art./page)", "pages", "2"),
                  ("Comparables min", "min_comps", "3"),
                  ("Coef. prix de vente réel", "discount", "0.9")]
        for i, (label, key, default) in enumerate(fields):
            r, c = divmod(i, 3)
            ttk.Label(form, text=label).grid(row=r, column=c * 2, sticky="e", padx=4, pady=2)
            v = tk.StringVar(value=default)
            self.vars[key] = v
            ttk.Entry(form, textvariable=v, width=22).grid(row=r, column=c * 2 + 1, padx=4)

        btns = ttk.Frame(self, padding=(8, 0))
        btns.pack(fill="x")
        self.go = ttk.Button(btns, text="Analyser", command=self.start)
        self.go.pack(side="left")
        ttk.Button(btns, text="Exporter CSV", command=self.export).pack(side="left", padx=6)
        self.status = ttk.Label(btns, text="Prêt.")
        self.status.pack(side="left", padx=10)

        self.tree = ttk.Treeview(self, columns=self.COLS, show="headings")
        for c in self.COLS:
            self.tree.heading(c, text=c, command=lambda col=c: self.sort_by(col))
            self.tree.column(c, width=260 if c == "Article" else 95, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree.bind("<Double-1>", self.open_link)

    def log(self, msg):
        self.after(0, lambda: self.status.config(text=msg))

    def start(self):
        try:
            cfg = {
                "min_profit": float(self.vars["min_profit"].get()),
                "min_margin": float(self.vars["min_margin"].get()),
                "ship": float(self.vars["ship"].get()),
                "pack": float(self.vars["pack"].get()),
                "min_comps": int(self.vars["min_comps"].get()),
                "discount": float(self.vars["discount"].get()),
            }
            budget = float(self.vars["budget"].get())
            pages = int(self.vars["pages"].get())
        except ValueError:
            messagebox.showerror("Erreur", "Vérifie que les champs numériques sont corrects.")
            return
        query = self.vars["query"].get().strip()
        self.go.config(state="disabled")
        threading.Thread(target=self.work, args=(query, budget, pages, cfg), daemon=True).start()

    def work(self, query, budget, pages, cfg):
        try:
            self.log("Récupération des articles...")
            items = fetch_items(query, budget, pages, self.log)
            self.rows = analyze(items, cfg)
            self.after(0, self.fill)
            self.log(f"{len(items)} articles analysés, {len(self.rows)} opportunités.")
        except Exception as e:
            self.log(f"Erreur : {e}")
        finally:
            self.after(0, lambda: self.go.config(state="normal"))

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(i), values=[r[c] for c in self.COLS])

    def sort_by(self, col):
        rev = getattr(self, "_rev", False)
        self.rows.sort(key=lambda r: r[col], reverse=not rev)
        self._rev = not rev
        self.fill()

    def open_link(self, _):
        sel = self.tree.selection()
        if sel:
            webbrowser.open(self.rows[int(sel[0])]["Lien"])

    def export(self):
        if not self.rows:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(self.rows[0].keys()), delimiter=";")
                w.writeheader()
                w.writerows(self.rows)


if __name__ == "__main__":
    App().mainloop()

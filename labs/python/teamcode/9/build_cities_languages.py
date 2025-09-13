#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build RU & EN text files of populated places with coordinates and Google Maps links.
Now supports SMALLER SETTLEMENTS (villages/hamlets) via GeoNames datasets:

Datasets (choose with --dataset):
  - cities1000     : places with population > 1000 (default)
  - cities500      : places with population > 500 (adds smaller towns)
  - allcountries   : ALL features; we filter to feature class 'P' (populated places), includes villages/hamlets

Extra controls:
  --min-pop N            : minimum population threshold (default 1000 for cities*, sensible to set 1 for allcountries)
  --feature-class P      : by default only 'P' (populated places). You may change, but P is recommended.
  --out-ru / --out-en    : output file paths. No headers, just lines.
  --skip-languages       : skip CIA World Factbook language fetch (fast run)
  --max-workers          : parallelism for language fetch
  --continents EU AS ... : filter by GeoNames continent codes (EU AS NA SA AF OC AN)
  --no-cache             : disable language JSON cache
  --timeout              : HTTP timeout
  --no-progress          : turn off progress bars
  --progress-width N     : progress bar width

Outputs (no headers):
  RU: City(RU) — Region(RU) — Country — Population — LAT,LON — https://maps.google.com/?q=LAT,LON — langs
  EN: City(ASCII) — Region(EN) — Country — Population — LAT,LON — https://maps.google.com/?q=LAT,LON — langs
"""

import argparse
import io
import json
import os
import re
import sys
import time
import zipfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# -------------------- GeoNames sources --------------------
GEONAMES_URLS = {
    "cities1000": "https://download.geonames.org/export/dump/cities1000.zip",
    "cities500":  "https://download.geonames.org/export/dump/cities500.zip",
    "allcountries": "https://download.geonames.org/export/dump/allCountries.zip",
}
GEONAMES_COUNTRYINFO_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
GEONAMES_ALT_RU_URLS = [
    "https://download.geonames.org/export/dump/alternateNames/ru.zip",
    "https://download.geonames.org/export/dump/alternatenames/ru.zip",
]
GEONAMES_ALT_ALL_URL = "https://download.geonames.org/export/dump/alternateNamesV2.zip"

# -------------------- Factbook sources --------------------
FACTBOOK_MIRRORS = [
    "https://raw.githubusercontent.com/factbook/factbook.json/master",
    "https://cdn.jsdelivr.net/gh/factbook/factbook.json@master",
]
FACTBOOK_REGION_FOLDERS = [
    "africa","antarctica","australia-oceania","central-america-n-caribbean","central-asia",
    "east-n-southeast-asia","europe","middle-east","north-america","south-america","south-asia",
]

# -------------------- Continents --------------------
CONTINENT_MAP_RU = {
    "AF": "Африка","AS": "Азия","EU": "Европа","NA": "Северная Америка",
    "OC": "Океания","SA": "Южная Америка","AN": "Антарктида",
}
CONTINENT_MAP_EN = {
    "AF": "Africa","AS": "Asia","EU": "Europe","NA": "North America",
    "OC": "Oceania","SA": "South America","AN": "Antarctica",
}

LANG_PAIR_RE = re.compile(r"([A-Za-zÀ-ÖØ-öø-ÿĀ-žŽ\u0400-\u04FF\-\s'()./]+?)\s*(\d+(?:\.\d+)?)%")

# -------------------- Progress bar helpers --------------------
class ProgressBar:
    def __init__(self, total, width=40, label="", enabled=True):
        self.total = max(1, int(total))
        self.width = max(10, int(width))
        self.label = label
        self.enabled = enabled and sys.stdout.isatty()
        self._lock = threading.Lock()
        self.current = 0
        self._last_render = 0.0
    def update(self, delta, suffix=""):
        with self._lock:
            self.current += int(delta)
            self._render(suffix)
    def set(self, value, suffix=""):
        with self._lock:
            self.current = int(value)
            self._render(suffix)
    def _render(self, suffix=""):
        if not self.enabled: return
        now = time.time()
        if now - self._last_render < 1/30: return
        self._last_render = now
        cur = max(0, min(self.current, self.total))
        ratio = cur / self.total
        done = int(self.width * ratio)
        bar = "█"*done + "░"*(self.width-done)
        percent = f"{ratio*100:5.1f}%"
        sys.stdout.write(f"\r{self.label} [{bar}] {percent} {cur}/{self.total} {suffix}")
        sys.stdout.flush()
    def finish(self, suffix=""):
        if not self.enabled: return
        self.set(self.total, suffix=suffix)
        sys.stdout.write("\n"); sys.stdout.flush()

def human_size(n):
    units = ["B","KB","MB","GB","TB"]
    s = float(n)
    for u in units:
        if s < 1024 or u == units[-1]: return f"{s:.1f} {u}"
        s /= 1024.0

def http_stream(url, timeout=30, chunk=65536):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urlopen(req, timeout=timeout)
    length = resp.headers.get("Content-Length")
    total = int(length) if length and length.isdigit() else None
    return resp, total, chunk

def download_bytes_with_progress(url, label, timeout=30, chunk=65536, pbar_width=40, enabled=True):
    try:
        resp, total, chunk = http_stream(url, timeout=timeout, chunk=chunk)
        buf = io.BytesIO()
        if total is not None:
            p = ProgressBar(total=total, width=pbar_width, label=label, enabled=enabled)
            read = 0
            while True:
                data = resp.read(chunk)
                if not data: break
                buf.write(data); read += len(data)
                p.set(read, suffix=f"{human_size(read)}/{human_size(total)}")
            p.finish(suffix=f"{human_size(read)}/{human_size(total)}")
        else:
            read = 0
            while True:
                data = resp.read(chunk)
                if not data: break
                buf.write(data); read += len(data)
            print(f"{label} ✓ {human_size(read)}")
        return buf.getvalue()
    finally:
        try: resp.close()
        except Exception: pass

# -------------------- Loaders --------------------
def load_countryinfo(timeout=45, pbar_width=40, enabled=True):
    data = download_bytes_with_progress(GEONAMES_COUNTRYINFO_URL, "[2/7] countryInfo.txt", timeout=timeout, pbar_width=pbar_width, enabled=enabled)
    txt = data.decode("utf-8", errors="replace")
    countries = {}
    for line in txt.splitlines():
        if not line or line.startswith("#"): continue
        parts = line.split("\t")
        if len(parts) < 9: continue
        iso2 = parts[0].strip()
        fips = parts[3].strip() or None
        name = parts[4].strip()
        cont = parts[8].strip()
        countries[iso2] = {
            "name": name,
            "continent_ru": CONTINENT_MAP_RU.get(cont, cont),
            "continent_en": CONTINENT_MAP_EN.get(cont, cont),
            "fips": fips,
        }
    return countries

def download_dataset_zip(dataset, timeout=90, pbar_width=40, enabled=True):
    url = GEONAMES_URLS[dataset]
    label = f"[1/7] {dataset}.zip"
    data = download_bytes_with_progress(url, label, timeout=timeout, pbar_width=pbar_width, enabled=enabled)
    return data

def stream_rows_from_zip(data, inner_filename=None, label_prefix="[parse]", pbar_width=40, enabled=True):
    """Yield lines (bytes) from a zip member with a progress bar by bytes read."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # pick file
        if inner_filename:
            info = zf.getinfo(inner_filename)
        else:
            # Heuristic: cities*.txt or allCountries.txt
            guess = None
            for i in zf.infolist():
                if i.filename.endswith(".txt"):
                    guess = i; break
            info = guess or zf.infolist()[0]
        total = info.file_size or 1
        p = ProgressBar(total=total, width=pbar_width, label=label_prefix, enabled=enabled)
        with zf.open(info) as f:
            read = 0
            for raw in f:
                read += len(raw)
                if enabled: p.set(read)
                yield raw
        p.finish()

# -------------------- Factbook languages --------------------
def fetch_factbook_json(fips_lower, timeout=30):
    for base in FACTBOOK_MIRRORS:
        for folder in FACTBOOK_REGION_FOLDERS:
            url = f"{base}/{folder}/{fips_lower}.json"
            try:
                data = download_bytes_with_progress(url, f"      ↳ {fips_lower}.json", timeout=timeout, enabled=False)
                return json.loads(data.decode("utf-8", errors="replace"))
            except Exception:
                continue
    return None

def try_load_cached(cache_dir, key):
    fp = os.path.join(cache_dir, f"{key}.json")
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return None
    return None

def save_cache(cache_dir, key, obj):
    os.makedirs(cache_dir, exist_ok=True)
    try:
        with open(os.path.join(cache_dir, f"{key}.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception: pass

def extract_language_pairs(data):
    if not isinstance(data, dict): return []
    candidates = [
        ("People and Society", "Languages", "text"),
        ("People_and_Society", "Languages", "text"),
        ("people_and_society", "languages", "text"),
    ]
    txt = None
    for path in candidates:
        cur = data; ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur: cur = cur[k]
            else: ok = False; break
        if ok and isinstance(cur, str): txt = cur; break
    if not txt: return []
    pairs = LANG_PAIR_RE.findall(txt)
    if not pairs: return []
    cleaned = []
    for lang, pct in pairs:
        lang = re.sub(r"\s*\(.*?\)", "", lang).strip()
        lang = re.sub(r"[,;/]+$", "", lang).strip()
        if not lang: continue
        try: cleaned.append((lang, float(pct)))
        except ValueError: pass
    if not cleaned: return []
    agg = {}
    for lang, pct in cleaned: agg[lang] = agg.get(lang, 0.0) + pct
    top = sorted(agg.items(), key=lambda x: -x[1])
    if len(top) > 6:
        keep = top[:5]; rest = sum(p for _, p in top[5:]); keep.append(("Other", rest)); top = keep
    return [(n, round(p,1)) for n,p in top if p > 0.0]

def build_lang_cache(countries, iso2_list, max_workers=8, use_cache=True, timeout=30, pbar_width=40, enabled=True):
    cache_dir = "cache/factbook"; os.makedirs(cache_dir, exist_ok=True)
    result = {}; total = len(iso2_list)
    p = ProgressBar(total=total, width=pbar_width, label="[5/7] Languages", enabled=enabled)
    lock = threading.Lock(); stats = {"ok":0, "miss":0, "err":0}
    def work(iso2):
        fips = countries[iso2].get("fips"); 
        if not fips: return iso2, []
        key = fips.lower()
        if use_cache:
            cached = try_load_cached(cache_dir, key)
            if isinstance(cached, dict):
                return iso2, extract_language_pairs(cached)
        data = fetch_factbook_json(key, timeout=timeout)
        if data:
            if use_cache: save_cache(cache_dir, key, data)
            return iso2, extract_language_pairs(data)
        return iso2, None
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        fut_map = {ex.submit(work, iso2): iso2 for iso2 in iso2_list}
        done = 0
        for fut in as_completed(fut_map):
            iso2 = fut_map[fut]
            try: _, pairs = fut.result()
            except Exception: pairs = None
            with lock:
                if pairs is None: stats["err"] += 1; result[iso2] = []
                elif pairs:       stats["ok"]  += 1; result[iso2] = pairs
                else:             stats["miss"]+= 1; result[iso2] = []
                done += 1
                p.set(done, suffix=f"(ok:{stats['ok']} miss:{stats['miss']} err:{stats['err']})")
    p.finish(suffix=f"(ok:{stats['ok']} miss:{stats['miss']} err:{stats['err']})")
    return result

# -------------------- RU alternate names --------------------
def load_ru_names_map(timeout=60, pbar_width=40, enabled=True):
    for url in GEONAMES_ALT_RU_URLS:
        try:
            data = download_bytes_with_progress(url, "[3/7] ru.zip", timeout=timeout, pbar_width=pbar_width, enabled=enabled)
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                info = zf.infolist()[0]; total = info.file_size or 1
                p = ProgressBar(total=total, width=pbar_width, label="[3/7] parse ru.txt", enabled=enabled)
                names = {}; read = 0
                with zf.open(info) as f:
                    for raw in f:
                        read += len(raw); p.set(read)
                        try: line = raw.decode("utf-8", errors="ignore").rstrip("\n")
                        except Exception: continue
                        parts = line.split("\t")
                        if len(parts) < 4: continue
                        try: gid = int(parts[1])
                        except Exception: continue
                        alt = parts[3].strip()
                        is_pref = (len(parts) > 4 and parts[4] == "1")
                        cur = names.get(gid)
                        if cur is None or is_pref: names[gid] = alt
                p.finish(); return names
        except Exception: continue
    # fallback big dump
    data = download_bytes_with_progress(GEONAMES_ALT_ALL_URL, "[3/7] alternateNamesV2.zip", timeout=max(timeout,120), pbar_width=pbar_width, enabled=enabled)
    names = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = None
        try: info = zf.getinfo("alternateNamesV2.txt")
        except KeyError: info = zf.infolist()[0]
        total = info.file_size or 1
        p = ProgressBar(total=total, width=pbar_width, label="[3/7] filter ru from altNamesV2", enabled=enabled)
        read = 0
        with zf.open(info) as f:
            for raw in f:
                read += len(raw); p.set(read)
                try: line = raw.decode("utf-8", errors="ignore").rstrip("\n")
                except Exception: continue
                parts = line.split("\t")
                if len(parts) < 4: continue
                if parts[2] != "ru": continue
                try: gid = int(parts[1])
                except Exception: continue
                alt = parts[3].strip()
                is_pref = (len(parts) > 4 and parts[4] == "1")
                cur = names.get(gid)
                if cur is None or is_pref: names[gid] = alt
        p.finish()
    return names

# -------------------- Utilities --------------------
def format_langs(lang_pairs):
    if not lang_pairs: return "—"
    return "; ".join(f"{name} {pct:.1f}%" for name, pct in lang_pairs)

# -------------------- Main --------------------
def main():
    ap = argparse.ArgumentParser(description="RU & EN populated places (with villages/hamlets) from GeoNames")
    ap.add_argument("--dataset", choices=["cities1000","cities500","allcountries"], default="cities1000", help="GeoNames dataset")
    ap.add_argument("--min-pop", type=int, default=1000, help="Minimum population threshold")
    ap.add_argument("--feature-class", default="P", help="Feature class filter (default 'P' for populated places)")
    ap.add_argument("--out-ru", default="places_FULL_RU.txt", help="RU output path")
    ap.add_argument("--out-en", default="places_FULL_EN.txt", help="EN output path")
    ap.add_argument("--max-workers", type=int, default=8, help="Concurrent workers for language fetch")
    ap.add_argument("--skip-languages", action="store_true", help="Do not fetch CIA languages (fast)")
    ap.add_argument("--no-cache", action="store_true", help="Disable local caching for Factbook JSON")
    ap.add_argument("--continents", nargs="*", default=None, help="Filter by continent codes (EU AS NA SA AF OC AN)")
    ap.add_argument("--no-progress", action="store_true", help="Disable progress bars")
    ap.add_argument("--progress-width", type=int, default=40, help="Progress bar width")
    ap.add_argument("--timeout", type=int, default=45, help="HTTP timeout seconds")
    args = ap.parse_args()
    enabled = (not args.no_progress)

    # [1/7] dataset zip
    data = download_dataset_zip(args.dataset, timeout=args.timeout, pbar_width=args.progress_width, enabled=enabled)

    # [2/7] countryInfo
    countries = load_countryinfo(timeout=args.timeout, pbar_width=args.progress_width, enabled=enabled)

    # [3/7] RU alternate names
    ru_map = load_ru_names_map(timeout=max(args.timeout,60), pbar_width=args.progress_width, enabled=enabled)

    # [4/7] first pass: collect ISO2 set (for languages) and estimate rows
    iso2_set = set(); total_bytes = 1
    label = f"[4/7] scan {args.dataset}.txt"
    for raw in stream_rows_from_zip(data, inner_filename=None, label_prefix=label, pbar_width=args.progress_width, enabled=enabled):
        try: line = raw.decode("utf-8", errors="replace").rstrip("\n")
        except Exception: continue
        parts = line.split("\t")
        if len(parts) < 15: continue
        # Feature class filter
        if args.dataset == "allcountries":
            fcl = parts[6]
            if args.feature_class and fcl != args.feature_class:
                continue
        iso2 = parts[8].strip()
        pop_str = parts[14].strip() or "0"
        try: pop = int(pop_str)
        except ValueError: pop = 0
        if pop < args.min_pop: continue
        if iso2: iso2_set.add(iso2)

    # [5/7] languages
    if args.skip_languages:
        print("[5/7] Skipping languages (by --skip-languages).")
        lang_cache = {iso2: [] for iso2 in iso2_set if iso2 in countries}
    else:
        iso2_list = sorted([i for i in iso2_set if i in countries])
        lang_cache = build_lang_cache(
            countries=countries, iso2_list=iso2_list,
            max_workers=args.max_workers, use_cache=not args.no_cache,
            timeout=args.timeout, pbar_width=args.progress_width, enabled=enabled,
        )

    # Prepare continent filter
    allowed_conts = set(args.continents) if args.continents else None
    rev_cont_ru = {v:k for k,v in CONTINENT_MAP_RU.items()}

    # [6/7] second pass: write outputs
    p = ProgressBar(total=100, width=args.progress_width, label="[6/7] Writing", enabled=enabled)  # will switch to indeterminate by % of bytes
    # Determine file to read again
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # pick txt
        info = None
        for i in zf.infolist():
            if i.filename.endswith(".txt"): info = i; break
        info = info or zf.infolist()[0]
        total = info.file_size or 1
        written = 0; read = 0
        with zf.open(info) as f, \
             open(args.out_ru, "w", encoding="utf-8", newline="") as out_ru, \
             open(args.out_en, "w", encoding="utf-8", newline="") as out_en:
            for raw in f:
                read += len(raw)
                # update pseudo-progress by bytes
                p.set(min(100, int(read/total*100)), suffix=f"{human_size(read)}/{human_size(total)}")
                try: line = raw.decode("utf-8", errors="replace").rstrip("\n")
                except Exception: continue
                parts = line.split("\t")
                if len(parts) < 15: continue
                if args.dataset == "allcountries":
                    fcl = parts[6]
                    if args.feature_class and fcl != args.feature_class: continue
                # population filter
                pop_str = parts[14].strip() or "0"
                try: pop = int(pop_str)
                except ValueError: pop = 0
                if pop < args.min_pop: continue
                # extract fields
                try: gid = int(parts[0])
                except Exception: continue
                name = parts[1].strip()
                asciiname = parts[2].strip() or name
                lat = parts[4]; lon = parts[5]
                iso2 = parts[8].strip()
                cmeta = countries.get(iso2)
                if not cmeta: continue
                # continent filter
                if allowed_conts:
                    code = rev_cont_ru.get(cmeta["continent_ru"])
                    if code not in allowed_conts: continue
                # build line
                city_ru = ru_map.get(gid, name)
                city_en = asciiname
                region_ru = cmeta["continent_ru"]; region_en = cmeta["continent_en"]
                country = cmeta["name"]
                latlon = f"{float(lat):.6f},{float(lon):.6f}"
                gmaps = f"https://maps.google.com/?q={latlon}"
                langs_str = format_langs(lang_cache.get(iso2, []))
                pop_fmt = f"{pop:,}".replace(",", " ")
                out_ru.write(f"{city_ru} — {region_ru} — {country} — {pop_fmt} — {latlon} — {gmaps} — {langs_str}\n")
                out_en.write(f"{city_en} — {region_en} — {country} — {pop_fmt} — {latlon} — {gmaps} — {langs_str}\n")
                written += 1
    p.finish(suffix=f"(lines: {written})")

    print(f"[7/7] Done. Wrote {written} lines to {args.out_ru} and {args.out_en}.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr); sys.exit(130)

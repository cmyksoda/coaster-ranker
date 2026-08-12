#!/usr/bin/env python3
"""Pre-populate the image cache for whole park chains.

Runs against the same cache directory and sqlite index the app uses, so anything
warmed here is served instantly (and without touching RCDB) when a visitor
imports a list. Safe to re-run: already-cached coasters are skipped.

    docker exec -it coaster-ranker python3 /app/warm_cache.py --list
    docker exec -it coaster-ranker python3 /app/warm_cache.py --chains disney,universal
    docker exec -it coaster-ranker python3 /app/warm_cache.py --all

Pacing comes from MIN_INTERVAL (seconds between outbound requests, default 0.34).
Raise it if you want to be gentler on RCDB:

    docker exec -e MIN_INTERVAL=1.0 -it coaster-ranker python3 /app/warm_cache.py --all
"""

import argparse
import glob
import io
import os
import re
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend as b  # noqa: E402


# Entries are either a park name (resolved via RCDB search) or a
# (name, rcdb_path) pair. Pin the path wherever the search is unreliable:
# searching "Silver Dollar City" returns /4593.htm, which is *Dollywood* - so
# an unpinned entry silently cached the wrong park's coasters.
CHAINS = {
    "cedar-fair": [
        ("Cedar Point", "/4529.htm"),
        "Kings Island",
        "Canada's Wonderland",
        "Knott's Berry Farm",
        "Carowinds",
        "Kings Dominion",
        "California's Great America",
        "Dorney Park",
        "Valleyfair",
        "Worlds of Fun",
        "Michigan's Adventure",
    ],
    "six-flags": [
        "Six Flags Magic Mountain",
        "Six Flags Great Adventure",
        "Six Flags Great America",
        "Six Flags Over Texas",
        "Six Flags Over Georgia",
        "Six Flags Fiesta Texas",
        "Six Flags St. Louis",
        "Six Flags New England",
        "Six Flags Discovery Kingdom",
        "Six Flags México",
        "La Ronde",
        "Six Flags Darien Lake",
        "Frontier City",
        ("Six Flags Great Escape", "/4596.htm"),
        # Closed after the 2025 season; listed for completeness, returns nothing.
        ("Six Flags America", "/4558.htm"),
    ],
    "seaworld": [
        "SeaWorld Orlando",
        "SeaWorld San Antonio",
        "SeaWorld San Diego",
        "Busch Gardens Tampa Bay",
        "Busch Gardens Williamsburg",
        ("Sesame Place Philadelphia", "/4710.htm"),
        ("Sesame Place San Diego", "/17798.htm"),
        ("SeaWorld Abu Dhabi", "/19054.htm"),
    ],
    # Merlin runs Legoland and the big British parks; grouped as one operator.
    "merlin": [
        "Alton Towers",
        "Thorpe Park",
        "Chessington World of Adventures",
        "Heide Park",
        "Gardaland",
        "Legoland Windsor",
        "Legoland Billund",
        "Legoland Deutschland",
        "Legoland California",
        "Legoland Florida",
        "Legoland Japan",
        "Legoland Malaysia",
        "Legoland New York",
    ],
    # Palace Entertainment: several well-known US parks under one owner.
    "palace": [
        "Kennywood",
        "Idlewild",
        "Lake Compounce",
        "Dutch Wonderland",
        "Story Land",
        "Splish Splash",
    ],
    "parques-reunidos": [
        "Mirabilandia",
        "Bobbejaanland",
        "Movie Park Germany",
        "Parque de Atracciones de Madrid",
        "Parque Warner Madrid",
        "Tusenfryd",
    ],
    # Compagnie des Alpes -- the Walibi/Parc Asterix group.
    "cda": [
        "Parc Asterix",
        "Walibi Belgium",
        "Walibi Holland",
        "Walibi Rhone-Alpes",
        "Bellewaerde",
        "Futuroscope",
    ],
    "herschend": [
        "Dollywood",
        ("Silver Dollar City", "/4579.htm"),
        "Wild Adventures",
        "Kentucky Kingdom",
    ],
    "hershey": [
        "Hersheypark",
    ],
    "universal": [
        ("Universal Studios Florida", "/4736.htm"),
        ("Universal's Islands of Adventure", "/4734.htm"),
        ("Universal Epic Universe", "/17569.htm"),
        ("Universal Studios Hollywood", "/5265.htm"),
        ("Universal Studios Japan", "/5492.htm"),
        ("Universal Studios Singapore", "/4859.htm"),
        ("Universal Studios Beijing", "/17287.htm"),
    ],
    "disney": [
        ("Walt Disney World - Magic Kingdom", "/4597.htm"),
        ("Walt Disney World - Epcot", "/15503.htm"),
        ("Walt Disney World - Disney's Hollywood Studios", "/4735.htm"),
        ("Walt Disney World - Disney's Animal Kingdom", "/5109.htm"),
        ("Disneyland", "/4571.htm"),
        "Disney California Adventure Park",
        ("Tokyo Disneyland", "/4959.htm"),
        ("Tokyo DisneySea", "/5073.htm"),
        ("Disneyland Paris - Disneyland Park", "/4864.htm"),
        ("Disneyland Paris - Walt Disney Studios Park", "/5054.htm"),
        ("Hong Kong Disneyland", "/5279.htm"),
        ("Shanghai Disneyland", "/6556.htm"),
    ],
    # ---- independents -------------------------------------------------------
    # Grouped by region so a run can be scoped. RCDB names both Florida Fun
    # Spots plain "Fun Spot America", so those three are pinned by id.
    "indie-us": [
        "Knoebels Amusement Resort",
        # Plain "Holiday World" finds a Canary Islands park first.
        ("Holiday World", "/4554.htm"),
        "Lagoon",
        "Silverwood",
        "Waldameer",
        "Lakemont Park",
        "DelGrosso's Amusement Park",
        "Conneaut Lake Park",
        "Indiana Beach",
        ("Adventureland", "/4676.htm"),          # Farmingdale, NY
        ("Adventureland Iowa", "/4576.htm"),     # Altoona, IA
        "Arnolds Park",
        "Beech Bend",
        "Santa Cruz Beach Boardwalk",
        "Belmont Park",
        "Morey's Piers",
        "Casino Pier",
        "Seabreeze",
        "Canobie Lake Park",
        "Funtown Splashtown USA",
        "Palace Playland",
        "Quassy Amusement Park",
        "Lake Winnepesaukah",
        "Camden Park",
        "Magic Springs",
        "Elitch Gardens",
        "Cliff's Amusement Park",
        # Search finds a same-named Minneapolis park first.
        ("Wonderland Amusement Park", "/4594.htm"),   # Amarillo, TX
        "Kemah Boardwalk",
        ("Nickelodeon Universe", "/4547.htm"),            # Mall of America
        ("Nickelodeon Universe American Dream", "/15593.htm"),
        # Home of the Cyclone. Not findable by name -- RCDB's search returns
        # nothing for "Luna Park" or "Coney Island".
        ("Luna Park Coney Island", "/9250.htm"),
        "Deno's Wonder Wheel Amusement Park",
        "Steel Pier",
        "Mt. Olympus",
        "Little Amerricka",
        "Bay Beach Amusement Park",
        "Oaks Amusement Park",
        ("Trimper Rides", "/4603.htm"),
        ("Martin's Fantasy Island", "/4732.htm"),
        "Santa's Village",
        "Keansburg Amusement Park",
        "Alabama Splash Adventure",
        # RCDB calls both Florida parks plain "Fun Spot America", so all three
        # are pinned -- searching by name cannot tell them apart.
        ("Fun Spot America Kissimmee", "/6373.htm"),
        ("Fun Spot America Orlando", "/10346.htm"),
        # Permanently closed; listed for completeness, returns nothing.
        ("Fun Spot America Atlanta", "/6125.htm"),
    ],
    "indie-europe": [
        "Europa-Park",
        "Efteling",
        "Phantasialand",
        "Liseberg",
        "PortAventura Park",
        "Energylandia",
        "Blackpool Pleasure Beach",
        "Toverland",
        "Plopsaland De Panne",
        "Tripsdrill",
        "Hansa-Park",
        "Holiday Park",
        "Bayern-Park",
        "Tivoli Gardens",
        "Bakken",
        "Djurs Sommerland",
        "Farup Sommerland",
        "Grona Lund",
        "Linnanmaki",
        "Sarkanniemi",
        "PowerPark",
        "Legendia",
        "Cinecitta World",
        "Rainbow MagicLand",
        "Ferrari Land",
        # Search finds a Venezuelan park of the same name first.
        ("Isla Magica", "/4867.htm"),
        "Terra Mitica",
        "Tibidabo",
        "Drayton Manor",
        "Flamingo Land",
        "Paultons Park",
        "Oakwood",
        "Emerald Park",
        "Pleasurewood Hills",
        "Fantasy Island",
        "Nigloland",
        "Le PAL",
        "Fraispertuis City",
        "Duinrell",
        ("Slagharen Themepark & Resort", "/4842.htm"),
        ("BonBon-Land", "/5015.htm"),
        "Wiener Prater",
        "Familypark",
        "Sochi Park",
    ],
    "indie-asia": [
        "Fuji-Q Highland",
        "Nagashima Spa Land",
        "Yomiuriland",
        "Hirakata Park",
        "Tobu Zoo",
        "Everland",
        "Lotte World",
        "Gyeongju World",
        "E-World",
        "Chimelong Paradise",
        # RCDB calls all six plain "Happy Valley"; only the id separates them.
        ("Happy Valley Beijing", "/5689.htm"),
        ("Happy Valley Shanghai", "/6259.htm"),
        ("Happy Valley Shenzhen", "/5166.htm"),
        ("Happy Valley Chengdu", "/6253.htm"),
        ("Happy Valley Wuhan", "/9221.htm"),
        ("Happy Valley Tianjin", "/10352.htm"),
        "Ocean Park",
        ("Sunac Land Chengdu", "/14946.htm"),
        "Ferrari World Abu Dhabi",
        "Warner Bros. World Abu Dhabi",
        "IMG Worlds of Adventure",
        ("Motiongate", "/13813.htm"),
        "Siam Park City",
        "Dream World",
        "Genting SkyWorlds",
        "Vinpearl Land",
    ],
    "indie-latam": [
        "Parque del Cafe",
        "Beto Carrero World",
        "Hopi Hari",
        "Fantasilandia",
        "Parque de la Costa",
        "Selva Magica",
        "Mundo Petapa",
        ("Mundo Aventura", "/5047.htm"),
    ],
    "indie-oceania": [
        # Both would otherwise match a same-named park abroad: "Dreamworld"
        # finds Thailand's Dream World, "Adventure World" finds Six Flags America.
        ("Dreamworld", "/4938.htm"),
        ("Adventure World", "/4937.htm"),
        "Warner Bros. Movie World",
        "Sea World",
        "Rainbow's End",
        "Gold Reef City",
    ],
}


def split_entry(entry):
    """CHAINS entries are either "Name" or ("Name", "/1234.htm")."""
    if isinstance(entry, tuple):
        return entry[0], entry[1]
    return entry, None


def park_listing(park_name, pinned=None):
    """(rcdb_name, [(path, coaster_name), ...]) for a park's operating coasters."""
    if pinned:
        pid = pinned
        # Seed the resolver cache so a visitor importing this park by name
        # lands on the same page instead of re-running the flaky search.
        # Keyed the same way resolve_park_id reads it back -- _norm alone skips
        # PARK_ALIASES, so pinned aliased parks were written where nothing looked.
        b.db_write("INSERT OR REPLACE INTO parks VALUES (?,?,?)",
                   (b.canonical_park(park_name), pid, int(time.time())))
    else:
        pid = b.resolve_park_id(park_name)
    if not pid:
        return None, []
    park_num = re.sub(r"\D", "", pid)
    try:
        html = b._get_text(f"{b.RCDB}/r.htm?ot=2&st=93&pk={park_num}")
    except Exception as e:
        print(f"    ! listing failed: {e}")
        return None, []
    rcdb_name = None
    m = re.search(r'href="?%s"?>([^<]+)</a>' % re.escape(pid), html)
    if m:
        rcdb_name = m.group(1)
    # No unfiltered retry on an empty result -- see park_coasters. A closed park
    # legitimately has nothing operating, and warming its removed rides would
    # put coasters nobody can ride into everyone's import.
    return rcdb_name, b.parse_listing(html)


def already_cached(name, park):
    rows = b.db_query("SELECT ckey FROM lookup WHERE qkey=?", (b._qkey(name, park),))
    return bool(rows)


def forget(name, park):
    """Drop the cached pick so the image gets chosen again from scratch."""
    b.db_write("DELETE FROM lookup WHERE qkey=?", (b._qkey(name, park),))


def reject_and_repick(specs, reason="review"):
    """specs are "Coaster Name @ Park". Blacklists the current image, re-picks.

    Re-picks from the coaster's already-known RCDB page rather than re-running
    the name search, so the right ride is guaranteed and it costs one request.
    """
    for spec in specs:
        name, _, park = spec.partition("@")
        name, park = name.strip(), park.strip()
        qkey = b._qkey(name, park)
        rows = b.db_query("SELECT ckey, rcdb, ctype FROM lookup WHERE qkey=?", (qkey,))
        if not rows:
            print(f"  ? {spec}: not in cache")
            continue
        ckey, rcdb_path, ctype = rows[0]

        if ckey:
            src = b.db_query("SELECT src FROM images WHERE ckey=?", (ckey,))
            if src:
                b.db_write("INSERT OR REPLACE INTO rejects VALUES (?,?,?)",
                           (src[0][0], reason, int(time.time())))

        if not rcdb_path:
            b.db_write("DELETE FROM lookup WHERE qkey=?", (qkey,))
            print(f"  ! {spec}: image came from a non-RCDB fallback; cleared for re-resolve")
            continue

        rejected = {r[0] for r in b.db_query("SELECT src FROM rejects")}
        try:
            html = b._get_text(b.RCDB + rcdb_path)
        except Exception as e:
            print(f"  ! {spec}: {e}")
            continue

        img_path, w, h = b.extract_best_image(html, exclude=rejected)
        new_ckey = None
        if img_path:
            try:
                new_ckey = b.cache_image(b.RCDB + img_path)
            except Exception:
                new_ckey = None
        b.db_write("INSERT OR REPLACE INTO lookup VALUES (?,?,?,?,?)",
                   (qkey, new_ckey, ctype, rcdb_path, int(time.time())))
        if new_ckey:
            print(f"  + {spec}: re-picked {w}x{h}")
        else:
            print(f"  - {spec}: no alternative image on the page")


def use_alt_source(specs):
    """Try Coasterpedia/Wikipedia for coasters where RCDB has no usable ride shot.

    Some rides - indoor/dark ones, heavily themed ones, brand-new ones - simply
    have no wide landscape photo on RCDB; the page is all signage, queue shots
    and construction. Rather than cycle through those, look elsewhere. If
    nothing turns up, clear the image so the UI falls back to the manufacturer
    chip, which reads as deliberate instead of showing a building site.
    """
    fixed = chipped = 0
    for spec in specs:
        nm, _, park = spec.partition("@")
        qkey = b._qkey(nm.strip(), park.strip())
        rows = b.db_query("SELECT ckey, rcdb, ctype FROM lookup WHERE qkey=?", (qkey,))
        if not rows:
            print(f"  ? {spec}: not in cache")
            continue
        ckey, rcdb_path, ctype = rows[0]

        # Recover the ride's real name+park from RCDB so the search is decent;
        # the cache key is normalised and makes a poor search term.
        real_name, real_park = nm.strip(), park.strip()
        if rcdb_path:
            try:
                html = b._get_text(b.RCDB + rcdb_path)
                # RCDB titles read "Ride - Park (City, State, Country)", and the
                # park itself may contain " - " (Walt Disney World - Epcot), so
                # split on the FIRST separator only.
                m = re.search(r"<title>([^<(]+?)\s*\(([^)]*)\)", html)
                if m:
                    ride, sep, parkname = m.group(1).strip().partition(" - ")
                    real_name = ride.strip()
                    if parkname.strip():
                        real_park = parkname.strip()
                    else:
                        real_park = m.group(2).split(",")[0].strip()
            except Exception:
                pass

        if ckey:
            src = b.db_query("SELECT src FROM images WHERE ckey=?", (ckey,))
            if src:
                b.db_write("INSERT OR REPLACE INTO rejects VALUES (?,?,?)",
                           (src[0][0], "no usable rcdb shot", int(time.time())))

        url = (b.coasterpedia_image_url(real_name, real_park)
               or b.wikipedia_image_url(real_name, real_park))
        new_ckey = None
        if url:
            try:
                new_ckey = b.cache_image(url)
            except Exception:
                new_ckey = None

        b.db_write("INSERT OR REPLACE INTO lookup VALUES (?,?,?,?,?)",
                   (qkey, new_ckey, ctype, rcdb_path, int(time.time())))
        if new_ckey:
            fixed += 1
            print(f"  + {real_name} @ {real_park}: alt source")
        else:
            chipped += 1
            print(f"  = {real_name} @ {real_park}: no alt image, will show chip")
    print(f"\n{fixed} replaced from another source, {chipped} left to the chip")


def repick_blank():
    """Give every image-less coaster the best remaining shot on its RCDB page.

    An earlier pass blanked coasters when the alternate sources came up empty,
    without going back to RCDB - but these pages carry 20-40 photos and only
    the rejected one or two are off-limits. This walks the rest.
    """
    rows = b.db_query("SELECT qkey, rcdb, ctype FROM lookup WHERE ckey IS NULL")
    print(f"{len(rows)} coasters without an image\n")
    fixed = still = 0
    for qkey, rcdb_path, ctype in rows:
        if not rcdb_path:
            still += 1
            print(f"  = {qkey}: no RCDB page on record")
            continue
        rejected = {r[0] for r in b.db_query("SELECT src FROM rejects")}
        try:
            html = b._get_text(b.RCDB + rcdb_path)
        except Exception as e:
            still += 1
            print(f"  ! {qkey}: {e}")
            continue
        img_path, w, h = b.extract_best_image(html, exclude=rejected)
        ckey = None
        if img_path:
            try:
                ckey = b.cache_image(b.RCDB + img_path)
            except Exception:
                ckey = None
        b.db_write("INSERT OR REPLACE INTO lookup VALUES (?,?,?,?,?)",
                   (qkey, ckey, ctype, rcdb_path, int(time.time())))
        if ckey:
            fixed += 1
            print(f"  + {qkey.split('|')[0][:30]:32} {w}x{h}")
        else:
            still += 1
            print(f"  = {qkey.split('|')[0][:30]:32} nothing left on the page")
    print(f"\n{fixed} given an image, {still} still without one")


def reencode_all():
    """Re-derive every cached image from its stored original.

    Changing IMAGE_FORMAT or MAX_WIDTH costs nothing but CPU - the sources are
    already on disk, so RCDB is never contacted.
    """
    rows = b.db_query("SELECT ckey, ext FROM images")
    print(f"re-encoding {len(rows)} images as {b.IMAGE_FORMAT} "
          f"(max_width={b.MAX_WIDTH or 'source'})\n")
    before = after = done = skipped = 0
    for ckey, old_ext in rows:
        src = glob.glob(os.path.join(b.SOURCE_DIR, ckey + ".*"))
        if not src:
            skipped += 1
            continue
        old_path = os.path.join(b.IMAGE_DIR, f"{ckey}.{old_ext}")
        if os.path.isfile(old_path):
            before += os.path.getsize(old_path)
        raw = open(src[0], "rb").read()
        data, ext = b._encode(raw)
        new_path = os.path.join(b.IMAGE_DIR, f"{ckey}.{ext}")
        tmp = new_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, new_path)
        if ext != old_ext and os.path.isfile(old_path):
            os.remove(old_path)
        w = h = 0
        try:
            with Image.open(io.BytesIO(data)) as probe:
                w, h = probe.size
        except Exception:
            pass
        b.db_write("UPDATE images SET ext=?, bytes=?, width=?, height=? WHERE ckey=?",
                   (ext, len(data), w, h, ckey))
        after += len(data)
        done += 1
        print(f"  {done}/{len(rows)}  {ckey}  {len(data)/1024:>6.0f} KB  {w}x{h}")
    print(f"\nre-encoded {done}, skipped {skipped} (no stored original)")
    if before:
        print(f"{before/1048576:.0f} MB -> {after/1048576:.0f} MB  ({after/before*100:.0f}% of previous)")


def warm_park(park_name, pinned=None, dry_run=False, limit=None, refresh=False):
    rcdb_name, rows = park_listing(park_name, pinned)
    label = f"{park_name}" + (f"  (RCDB: {rcdb_name})" if rcdb_name and rcdb_name != park_name else "")
    if not rows:
        print(f"  {label}: no coasters found")
        return 0, 0, 0
    if limit:
        rows = rows[:limit]
    print(f"  {label}: {len(rows)} operating coasters")

    got = missed = skipped = 0
    for path, cname in rows:
        if refresh:
            forget(cname, park_name)
        elif already_cached(cname, park_name):
            skipped += 1
            continue
        if dry_run:
            print(f"    would fetch: {cname}")
            continue
        try:
            # The listing already told us this ride's page, so hand it straight
            # over rather than searching a name that other parks share too.
            url, ctype = b.resolve_coaster(cname, park_name, known_path=path)
        except Exception as e:
            print(f"    ! {cname}: {e}")
            missed += 1
            continue
        if url:
            got += 1
            print(f"    + {cname}  [{ctype}]")
        else:
            missed += 1
            print(f"    - {cname}  [{ctype}]  (no image found)")
    return got, missed, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chains", help="comma-separated chain keys (see --list)")
    ap.add_argument("--parks", help="comma-separated park names, bypassing the chain lists")
    ap.add_argument("--all", action="store_true", help="every chain")
    ap.add_argument("--list", action="store_true", help="show the chains and parks, then exit")
    ap.add_argument("--dry-run", action="store_true", help="show what would be fetched")
    ap.add_argument("--limit", type=int, help="cap coasters per park (handy for a smoke test)")
    ap.add_argument("--reject", action="append", metavar="'Name @ Park'",
                    help="blacklist the current image for this coaster and re-pick "
                         "(repeatable)")
    ap.add_argument("--alt-source", action="append", metavar="'Name @ Park'",
                    help="try Coasterpedia/Wikipedia for this coaster; fall back to the chip")
    ap.add_argument("--repick-blank", action="store_true",
                    help="give every image-less coaster the best remaining RCDB photo")
    ap.add_argument("--reencode", action="store_true",
                    help="re-derive cached images from stored originals after changing "
                         "IMAGE_FORMAT/MAX_WIDTH (no network)")
    ap.add_argument("--refresh", action="store_true",
                    help="re-pick images for coasters already cached (e.g. after changing the scorer)")
    args = ap.parse_args()

    if args.repick_blank:
        repick_blank()
        return

    if args.alt_source:
        use_alt_source(args.alt_source)
        return

    if args.reject:
        reject_and_repick(args.reject)
        return

    if args.reencode:
        reencode_all()
        return

    if args.list:
        for key, parks in CHAINS.items():
            print(f"{key}  ({len(parks)} parks)")
            for e in parks:
                nm, pid = split_entry(e)
                print(f"    {nm}" + (f"   [{pid}]" if pid else ""))
        return

    if args.parks:
        targets = [(p.strip(), p.strip(), None) for p in args.parks.split(",") if p.strip()]
    else:
        keys = list(CHAINS) if args.all else [k.strip() for k in (args.chains or "").split(",") if k.strip()]
        if not keys:
            ap.error("give me --chains, --parks, or --all (or --list to see what's available)")
        unknown = [k for k in keys if k not in CHAINS]
        if unknown:
            ap.error(f"unknown chain(s): {', '.join(unknown)}. Known: {', '.join(CHAINS)}")
        targets = [(k,) + split_entry(e) for k in keys for e in CHAINS[k]]

    print(f"format={b.IMAGE_FORMAT}  max_width={b.MAX_WIDTH or 'source'}  "
          f"pacing={b.MIN_INTERVAL}s  cache={b.CACHE_DIR}")
    print()

    start = time.time()
    tot_got = tot_missed = tot_skipped = 0
    current = None
    for chain, park, pinned in targets:
        if chain != current and not args.parks:
            current = chain
            print(f"[{chain}]")
        g, m, s = warm_park(park, pinned, dry_run=args.dry_run, limit=args.limit,
                            refresh=args.refresh)
        tot_got += g
        tot_missed += m
        tot_skipped += s

    n_img, total = b.db_query("SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM images")[0]
    mins = (time.time() - start) / 60
    print()
    print(f"done in {mins:.1f} min - {tot_got} fetched, {tot_missed} without an image, "
          f"{tot_skipped} already cached")
    print(f"cache now holds {n_img} images, {total / 1048576:.0f} MB "
          f"(avg {total / max(1, n_img) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

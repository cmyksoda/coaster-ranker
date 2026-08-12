# Coaster Ranker

A solution for ranking your coaster credits that accounts for the implicit bias of trying to have the right opinion.  

Derived from [this repository](https://github.com/coasterrankerburner-gif/Claude-Pairwise-Coaster-Ranker) and made non-Claude centric, this tool lets you import your coaster list (via `.csv`, `.json`, `.md`, or plaintext) and sequentially rank them 1-on-1 to produce an unbiased ranking. Choose to run through your whole list of credits, your top 50, 25, or 10. It's best to include the park name along with the coaster, especially for ambiguously named rides (e.g., Batman, Goliath).  

Live site is here: https://coasters.cmyksoda.cc  

## Self-Hosting

Hosting this yourself is dead simple and lives in one docker container. A `docker-compose.yml` is already provided.

1. Clone this repository.
2. Run `docker compose up -d`
3. Coaster ranker now lives at http://localhost:8192
### Optional: pre-load coaster images

Out of the box the server fetches images from RCDB on demand, so the first person to import a list waits a moment per coaster. You can pre-load whole park chains instead:

```
docker exec -it coaster-ranker python3 /app/warm_cache.py --list
docker exec -it coaster-ranker python3 /app/warm_cache.py --all
```

`--all` covers Cedar Fair, Six Flags, SeaWorld/Busch Gardens, Herschend, Hersheypark, Universal, Disney, Merlin, Palace, Parques Reunidos and Compagnie des Alpes, plus a bunch more  well-known independents worldwide — 219 parks, around 1,200 coasters and ~200 MB — and takes an hour or so. Images land in `./cache`, mounted as a volume, so rebuilding the container never re-downloads anything.

**Please be kind to RCDB.** `MIN_INTERVAL` (default `0.34`) is the minimum gap in seconds between outbound requests, and every visitor's imports share it.

### Putting it in front of other people

If you're exposing this via a tunnel or reverse proxy, set `ALLOWED_ORIGINS` in `docker-compose.yml` to your domain. It defaults to `*`, which lets any website drive your API from its visitors' browsers.

Each visitor's coaster list and ranking live only in their own browser (localStorage for the list, IndexedDB for images), so there's no database and nothing of theirs on your server. The image cache is server-side and shared, so one download serves everybody.

### Image quality

`IMAGE_FORMAT` and `MAX_WIDTH` control how cached images are stored — the default is `webp-q85` at 1100px, enough for a 2x display at the widest the card ever renders, averaging ~140 KB an image. Originals are kept alongside in `cache/source`, so changing either setting only costs CPU:

```
docker exec -it coaster-ranker python3 /app/warm_cache.py --reencode
```

Coaster data and photography come from [RCDB](https://rcdb.com), with Coasterpedia and Wikipedia as fallbacks.  
Color schemes are [Catppuccin](https://github.com/catppuccin) Latte (Light) and Mocha (Dark).


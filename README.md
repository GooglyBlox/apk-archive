# APK Archive

A searchable index of Android packages hosted on the Internet Archive.

Files are served by archive.org. Nothing is hosted here.

## Run

```sh
pip install -r requirements.txt

python -m crawler discover   # find IA items
python -m crawler crawl      # list .apk files inside them
python -m crawler enrich     # read package/version/SDK/icon from each
python -m crawler export --out web/data/apk.sqlite
```

Every phase is resumable and bounded by `--budget` seconds.

Serve the site locally (needs HTTP Range support):

```sh
python tools/serve.py --dir web
```

## Sources

Add Internet Archive queries or item identifiers to `data/sources.json`.

## Automation

GitHub Actions handles crawling, enrichment and deployment. The crawler
database lives as a release asset; the site publishes to GitHub Pages.

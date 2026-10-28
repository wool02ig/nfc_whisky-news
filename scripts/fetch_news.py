#!/usr/bin/env python3
"""Collect whisky news from Japanese/English Google News RSS feeds."""
import datetime as dt
import email.utils
import html
from html.parser import HTMLParser
import hashlib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data' / 'news.json'
NOW = dt.datetime.now(dt.timezone.utc)
LIMIT_DAYS = 30
MAX_ITEMS = 100
QUERIES = [
    ('ja', 'JP', 'JP:ja', 'ウイスキー 新発売 OR 新商品 OR 新作'),
    ('ja', 'JP', 'JP:ja', 'ウイスキー 限定 OR 抽選 OR 蒸留所'),
    ('ja', 'JP', 'JP:ja', 'ジャパニーズウイスキー 蒸留所 OR 発売 OR 価格'),
    ('en-GB', 'GB', 'GB:en', 'whisky new release OR limited edition'),
    ('en-GB', 'GB', 'GB:en', 'whisky distillery OR scotch whisky news'),
    ('en-US', 'US', 'US:en', 'whiskey bourbon new release OR distillery'),
    ('en-US', 'US', 'US:en', 'Irish whiskey OR Japanese whisky new release'),
]

CATEGORIES = [
    ('limited', re.compile(r'限定|抽選|数量限定|lottery|limited|exclusive|allocation|raffle', re.I)),
    ('distillery', re.compile(r'蒸留所|蒸溜所|distillery|distilleries|visitor centre|visitor center|opening', re.I)),
    ('new', re.compile(r'新発売|新商品|新作|新登場|release|launch|unveil|debut|introduce|edition|bottling', re.I)),
]
WHISKY = re.compile(r'ウイスキー|ウィスキー|whisk(?:y|ey)|bourbon|scotch|シングルモルト|バーボン|蒸留所|蒸溜所', re.I)

def category(title):
    for key, pattern in CATEGORIES:
        if pattern.search(title):
            return key
    return 'industry'

def get_text(element, name):
    return (element.findtext(name) or '').strip()

def fetch_feed(locale, country, ceid, query):
    params = urllib.parse.urlencode({'q': query + ' when:30d', 'hl': locale, 'gl': country, 'ceid': ceid})
    url = 'https://news.google.com/rss/search?' + params
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; NFCWhiskyNews/1.0)'})
    with urllib.request.urlopen(req, timeout=25) as res:
        content = res.read(2_000_000)
    root = ET.fromstring(content)
    return root.findall('./channel/item')

def load_existing():
    if not OUTPUT.exists():
        return []
    try:
        return json.loads(OUTPUT.read_text(encoding='utf-8')).get('articles', [])
    except (ValueError, OSError):
        return []


MEDIA_NS = '{http://search.yahoo.com/mrss/}'

class ImageMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image = None
    def handle_starttag(self, tag, attrs):
        if tag != 'meta' or self.image:
            return
        data = dict(attrs)
        if data.get('property','').lower() in ('og:image', 'og:image:url') or data.get('name','').lower() == 'twitter:image':
            self.image = data.get('content')

def is_https_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        return parts.scheme == 'https' and bool(parts.hostname) and not parts.username and not parts.password
    except (ValueError, TypeError):
        return False

def rss_image(entry):
    for element in entry.iter():
        if element.tag in (MEDIA_NS + 'thumbnail', MEDIA_NS + 'content', 'enclosure'):
            candidate = element.get('url','')
            if is_https_url(candidate) and (element.tag != 'enclosure' or element.get('type','').startswith('image/')):
                return candidate
    return None

def article_image(url):
    # Google News links are intermediate links, not publisher articles.
    if not is_https_url(url) or urllib.parse.urlsplit(url).hostname == 'news.google.com':
        return None
    try:
        request = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (NFCWhiskyNews/1.4)'})
        with urllib.request.urlopen(request, timeout=5) as response:
            if 'text/html' not in response.headers.get('Content-Type',''):
                return None
            final_url = response.geturl()
            document = response.read(150_000).decode('utf-8', errors='replace')
        parser = ImageMetaParser()
        parser.feed(document)
        image_url = urllib.parse.urljoin(final_url, html.unescape(parser.image or ''))
        return image_url if parser.image and is_https_url(image_url) else None
    except Exception:
        return None

def main():
    items = {}
    successes = 0
    for locale, country, ceid, query in QUERIES:
        try:
            entries = fetch_feed(locale, country, ceid, query)
            successes += 1
        except Exception as error:
            print('Feed failed:', query, error)
            continue
        for entry in entries:
            title = re.sub(r'\s+', ' ', get_text(entry, 'title')).strip()
            link = get_text(entry, 'link')
            if not title or not link.startswith('https://') or not WHISKY.search(title):
                continue
            try:
                published = email.utils.parsedate_to_datetime(get_text(entry, 'pubDate'))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=dt.timezone.utc)
                published = published.astimezone(dt.timezone.utc)
            except (ValueError, TypeError):
                continue
            if not dt.timedelta() <= NOW - published <= dt.timedelta(days=LIMIT_DAYS):
                continue
            source = get_text(entry.find('source'), '') if False else ''
            source_node = entry.find('source')
            source = source_node.text.strip() if source_node is not None and source_node.text else 'Google News'
            # Merge identical stories syndicated via different region-specific feeds.
            canonical = re.sub(r'\s+-\s+[^-]+$', '', title).casefold().strip()
            canonical = re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff]', '', canonical)
            unique = hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]
            record = {'id': unique, 'title': title, 'url': link,
                      'source': source, 'publishedAt': published.isoformat().replace('+00:00', 'Z'),
                      'category': category(title), 'language': 'ja' if locale == 'ja' else 'en',
                      'region': 'domestic' if locale == 'ja' else 'international'}
            img = rss_image(entry)
            if img: record['image'] = img
            previous = items.get(unique)
            if not previous or previous['publishedAt'] < record['publishedAt']:
                items[unique] = record
    if successes == 0:
        print('All feeds failed; preserving existing data.')
        return
    for old in load_existing():
        try:
            published = dt.datetime.fromisoformat(old['publishedAt'].replace('Z', '+00:00'))
            if NOW - published <= dt.timedelta(days=LIMIT_DAYS):
                items.setdefault(old['id'], old)
        except (KeyError, ValueError, TypeError):
            continue
    # Reuse existing article photos across hourly runs.
    past = {a.get('id'): a for a in load_existing()}
    for item in items.values():
        older = past.get(item['id'], {})
        if not item.get('image') and is_https_url(older.get('image')):
            item['image'] = older['image']
        if older.get('imageChecked'):
            item['imageChecked'] = True
    tries = 0
    for item in sorted(items.values(), key=lambda x: x['publishedAt'], reverse=True):
        if tries >= 12:
            break
        if item.get('image') or item.get('imageChecked'):
            continue
        item['imageChecked'] = True
        # Direct publisher links can expose og:image; Google News RSS links generally cannot.
        if urllib.parse.urlsplit(item['url']).hostname == 'news.google.com':
            continue
        tries += 1
        image = article_image(item['url'])
        if image: item['image'] = image
    articles = sorted(items.values(), key=lambda x: x['publishedAt'], reverse=True)[:MAX_ITEMS]
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps({'updatedAt': NOW.isoformat().replace('+00:00', 'Z'),
                                  'count': len(articles), 'articles': articles}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Feeds {successes}/{len(QUERIES)}; saved {len(articles)} articles')

if __name__ == '__main__':
    main()

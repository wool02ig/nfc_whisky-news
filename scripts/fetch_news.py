#!/usr/bin/env python3
"""Collect whisky news from specialist RSS feeds and targeted Google News RSS."""
import datetime as dt
import email.utils
import html
from html.parser import HTMLParser
import hashlib
import json
import re
import difflib
import ipaddress
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data' / 'news.json'
NOW = dt.datetime.now(dt.timezone.utc)
LIMIT_DAYS = 30
MAX_ITEMS = 120
QUERIES = [
    ('ja', 'JP', 'JP:ja', 'ウイスキー 新発売 OR 新商品 OR 新作'),
    ('ja', 'JP', 'JP:ja', 'ウイスキー 限定 OR 抽選 OR 蒸留所'),
    ('ja', 'JP', 'JP:ja', 'ジャパニーズウイスキー 蒸留所 OR 発売 OR 価格'),
    ('en-GB', 'GB', 'GB:en', 'whisky new release OR limited edition'),
    ('en-GB', 'GB', 'GB:en', 'whisky distillery OR scotch whisky news'),
    ('en-US', 'US', 'US:en', 'whiskey bourbon new release OR distillery'),
    ('en-US', 'US', 'US:en', 'Irish whiskey OR Japanese whisky new release'),
    # Sources without dependable public feeds: use Google News as a discovery layer.
    ('ja', 'JP', 'JP:ja', 'site:whisky-barrel.com ウイスキー'),
    ('ja', 'JP', 'JP:ja', 'site:suntory.co.jp/news ウイスキー'),
    ('ja', 'JP', 'JP:ja', 'site:nikka.com ウイスキー'),
    ('ja', 'JP', 'JP:ja', 'site:wakatsuru.co.jp 三郎丸 ウイスキー'),
    ('ja', 'JP', 'JP:ja', 'site:kanosuke.com ウイスキー'),
    ('en-GB', 'GB', 'GB:en', 'site:whiskyadvocate.com whisky'),
]

# Direct feeds (independent of Google News). A feed may be unavailable or blocked;
# failures are logged and do not stop other sources from being processed.
DIRECT_FEEDS = [
    ('The Whiskey Wash', 'https://thewhiskeywash.com/feed/', 'international'),
    ('The Spirits Business', 'https://www.thespiritsbusiness.com/feed/', 'international'),
    ('Whisky Advocate', 'https://whiskyadvocate.com/feed/', 'international'),
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
        return (parts.scheme == 'https' and bool(parts.hostname) and not parts.username
                and not parts.password and parts.hostname.lower() not in ('localhost',)
                and not parts.hostname.lower().endswith('.local')
                and (not re.fullmatch(r'[0-9.]+', parts.hostname) or ipaddress.ip_address(parts.hostname).is_global))
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

def fetch_xml(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; NFCWhiskyNews/1.6; RSS reader)'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return ET.fromstring(response.read(2_000_000))


def normalize_title(title):
    # Remove a trailing provider label, excess punctuation, and spacing.
    title = re.sub(r'\s+[–—-]\s+[^–—-]{2,45}$', '', title)
    return re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff]', '', title).casefold()


def story_id(title):
    return hashlib.sha256(normalize_title(title).encode('utf-8')).hexdigest()[:20]


def parsed_date(value):
    try:
        result = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError, IndexError):
        try:
            result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, TypeError, AttributeError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=dt.timezone.utc)
    return result.astimezone(dt.timezone.utc)


def image_from_entry(entry):
    url = rss_image(entry)
    if url:
        return url
    description = entry.findtext('description') or entry.findtext('{http://www.w3.org/2005/Atom}summary') or ''
    match = re.search(r'<img\b[^>]*?\bsrc=["\']([^"\']+)', html.unescape(description), re.I)
    return match.group(1) if match and is_https_url(match.group(1)) else None


def record_for(title, link, published, source, region, image=None):
    if not title or not is_https_url(link) or not WHISKY.search(title):
        return None
    if published is None or not dt.timedelta() <= NOW - published <= dt.timedelta(days=LIMIT_DAYS):
        return None
    record = {'id': story_id(title), 'title': title, 'url': link,
              'source': source, 'publishedAt': published.isoformat().replace('+00:00', 'Z'),
              'category': category(title), 'language': 'ja' if region == 'domestic' else 'en',
              'region': region}
    if image and is_https_url(image):
        record['image'] = image
    return record


def direct_entries(root):
    # Supports RSS 2.0 and Atom feeds.
    for entry in root.findall('./channel/item'):
        yield (get_text(entry, 'title'), get_text(entry, 'link'),
               get_text(entry, 'pubDate'), entry)
    ns = '{http://www.w3.org/2005/Atom}'
    for entry in root.findall(ns + 'entry'):
        title = (entry.findtext(ns + 'title') or '').strip()
        link = next((n.get('href', '') for n in entry.findall(ns + 'link') if n.get('rel', 'alternate') == 'alternate'), '')
        date = entry.findtext(ns + 'published') or entry.findtext(ns + 'updated') or ''
        yield (title, link, date, entry)


def add_record(items, rec):
    if not rec:
        return
    current = items.get(rec['id'])
    # Prefer direct publisher URLs and article images over Google redirect links.
    score = lambda x: (urllib.parse.urlsplit(x['url']).hostname != 'news.google.com', bool(x.get('image')))
    if current is None or score(rec) > score(current):
        if current and not rec.get('image') and current.get('image'):
            rec['image'] = current['image']
        items[rec['id']] = rec


def main():
    items = {}
    successes = 0
    for source, feed_url, region in DIRECT_FEEDS:
        try:
            root = fetch_xml(feed_url)
            count = 0
            for title, link, date, entry in direct_entries(root):
                record = record_for(title, link, parsed_date(date), source, region, image_from_entry(entry))
                if record:
                    add_record(items, record)
                    count += 1
            successes += 1
            print(f'Direct source {source}: {count} matched')
        except Exception as error:
            print(f'Direct feed failed ({source}): {error}')

    for locale, country, ceid, query in QUERIES:
        try:
            entries = fetch_feed(locale, country, ceid, query)
            successes += 1
        except Exception as error:
            print('Google News feed failed:', query, error)
            continue
        for entry in entries:
            title = re.sub(r'\s+', ' ', get_text(entry, 'title')).strip()
            link = get_text(entry, 'link')
            source_node = entry.find('source')
            source = source_node.text.strip() if source_node is not None and source_node.text else 'Google News'
            region = 'domestic' if locale == 'ja' else 'international'
            add_record(items, record_for(title, link, parsed_date(get_text(entry, 'pubDate')),
                                         source, region, image_from_entry(entry)))

    old_items = load_existing()
    if successes == 0:
        print('All feeds failed; preserving existing data.')
        return
    for old in old_items:
        try:
            published = parsed_date(old['publishedAt'])
            if published is not None and dt.timedelta() <= NOW - published <= dt.timedelta(days=LIMIT_DAYS):
                items.setdefault(old['id'], old)
        except (KeyError, ValueError, TypeError):
            continue
    past = {a.get('id'): a for a in old_items}
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
        if urllib.parse.urlsplit(item['url']).hostname == 'news.google.com':
            continue
        tries += 1
        picture = article_image(item['url'])
        if picture:
            item['image'] = picture
    # Catch moderately different headlines of the same story from syndication,
    # but never merge across languages or different publication days blindly.
    results = []
    for item in sorted(items.values(), key=lambda x: x['publishedAt'], reverse=True):
        match = next((other for other in results
                      if item['region'] == other['region']
                      and item['publishedAt'][:10] == other['publishedAt'][:10]
                      and difflib.SequenceMatcher(None, normalize_title(item['title']), normalize_title(other['title'])).ratio() >= .91), None)
        if match:
            # Prefer publisher direct links/images when near-identical headlines collide.
            if not match.get('image') and item.get('image'):
                match['image'] = item['image']
            continue
        results.append(item)
        if len(results) >= MAX_ITEMS:
            break
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps({'updatedAt': NOW.isoformat().replace('+00:00', 'Z'),
                                  'count': len(results), 'articles': results}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Feeds OK: {successes}/{len(QUERIES)+len(DIRECT_FEEDS)}; articles: {len(results)}')

if __name__ == '__main__':
    main()

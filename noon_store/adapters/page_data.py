"""Read the catalog data noon embeds in its store pages.

noon builds store pages on its server and embeds the data each page was built from, serialized by
seroval as a JavaScript expression:
    ...,nbHits:17635,nbPages:10,facets:$R[3]=[$R[4]={code:"price",data:$R[5]={max:690,min:18}}],hits:$R[6]=[...]
This module parses the subset of JavaScript that seroval writes for plain data: objects with bare or
quoted keys, arrays, strings, numbers, !0 and !1 (true, false), null, void 0 (undefined), and
$R[n]=value assignments with later $R[n] references to the same value. Anything else (a Date, a Map,
...) is read as None.
"""
import re
from typing import Optional

from ..domain import CatalogPage, Category, Product, StoreRef

IMAGE_URL = "https://f.nooncdn.com/p/{}.jpg?width=800"

_TOKEN = re.compile(r"""
    (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<ref>\$R\[(?P<id>\d+)\](?P<assign>=(?!=))?)
  | (?P<number>-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<word>!0|!1|void\s+0|-?Infinity|[A-Za-z_$][\w$]*)
  | (?P<punct>[{}\[\]():,])
  | (?P<space>\s+)
  | (?P<other>.)
""", re.X | re.S)

_WORDS = {"!0": True, "!1": False, "true": True, "false": False, "null": None, "undefined": None,
          "NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")}
_ESCAPE = re.compile(r"\\(?:x([0-9A-Fa-f]{2})|u([0-9A-Fa-f]{4})|u\{([0-9A-Fa-f]+)\}|(.))", re.S)
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0", "\n": ""}
_SURROGATE = re.compile("[\ud800-\udfff]")
_CATALOG = re.compile(r"[{,]nbHits:")
_OPENERS, _CLOSERS = "{[(", "}])"


class PageDataError(ValueError):
    """The page doesn't hold catalog data in the expected form."""


def read_page(html: str, store: StoreRef) -> CatalogPage:
    """The products and filters of one page of `store`."""
    catalog = read_catalog(html)
    facets = {f.get("code"): f.get("data") for f in catalog.get("facets") or () if isinstance(f, dict)}
    price = facets.get("price") if isinstance(facets.get("price"), dict) else {}
    # noon mixes other sellers' sponsored products (is_ad) into store pages
    hits = [hit for hit in catalog["hits"] if isinstance(hit, dict) and not hit.get("is_ad")]
    return CatalogPage(
        total=catalog["nbHits"],
        products=tuple(p for p in (_to_product(hit, store) for hit in hits) if p),
        store_name=_store_name(facets.get("partner"), hits),
        price_range=(price["min"], price["max"]) if _is_number(price.get("min")) and _is_number(price.get("max")) else None,
        categories=_to_categories(facets.get("category")),
    )


def _to_product(hit: dict, store: StoreRef) -> Optional[Product]:
    sku = str(hit.get("sku") or "").strip()
    if not sku:
        return None
    keys = hit.get("image_keys") or ([hit["image_key"]] if hit.get("image_key") else [])
    link = f"{store.site}/{store.locale}/{hit.get('url') or 'product'}/{sku}/p/"
    if hit.get("offer_code"):
        link += f"?o={hit['offer_code']}"
    price = hit.get("sale_price") or hit.get("price")
    return Product(sku=sku, title=str(hit.get("name") or ""), brand=str(hit.get("brand") or ""),
                   price=float(price) if _is_number(price) else None, link=link,
                   image_urls=tuple(IMAGE_URL.format(key) for key in keys if isinstance(key, str) and key))


def _store_name(partners, hits) -> str:
    """The seller's name, when the page lists a single seller. (Category pages also list sellers with no products.)"""
    names = {p.get("name") for p in partners or () if isinstance(p, dict) and p.get("count") != 0}
    if len(names) != 1:
        names = {hit.get("store_name") for hit in hits}
    return str(names.pop() or "") if len(names) == 1 else ""


def _to_categories(nodes) -> tuple[Category, ...]:
    return tuple(Category(n["code"], _to_categories(n.get("children")))
                 for n in nodes or () if isinstance(n, dict) and n.get("code"))


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def read_catalog(html):
    """The catalog a store page was built from: {"nbHits": int, "facets": [...], "hits": [...]}.

    hits are the page's products as noon describes them (sku, name, brand, price, image_keys, ...),
    facets the page's filters (price range, category tree, seller).
    """
    found = _CATALOG.search(html)
    if not found:
        raise PageDataError("The page has no catalog data.")
    catalog = _Parser(html, found.start() + 1).members({"nbHits", "facets", "hits"})
    if not isinstance(catalog.get("nbHits"), int) or not isinstance(catalog.get("hits"), list):
        raise PageDataError("The page's catalog data is incomplete.")
    return catalog


def _string(token):
    body = token[1:-1]
    if "\\" not in body:
        return body
    text = _ESCAPE.sub(_unescape, body)
    if _SURROGATE.search(text):  # an emoji written as a pair of \u escapes
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return text


def _unescape(match):
    code = match.group(1) or match.group(2) or match.group(3)
    if code:
        return chr(int(code, 16))
    return _SIMPLE_ESCAPES.get(match.group(4), match.group(4))


class _Parser:
    def __init__(self, text, position):
        self.tokens = _TOKEN.finditer(text, position)
        self.pushed_back = None
        self.refs = {}

    def next(self):
        if self.pushed_back is not None:
            token, self.pushed_back = self.pushed_back, None
            return token
        for token in self.tokens:
            if token.lastgroup != "space":
                return token
        raise PageDataError("The page's catalog data ends unexpectedly.")

    def expect(self, text):
        token = self.next()
        if token.group() != text:
            raise PageDataError(f"Expected {text!r} in the page's catalog data, found {token.group()!r}.")

    def members(self, wanted):
        """Read `key:value` pairs from the middle of an object until it closes or every wanted key is read.
        Values of keys that aren't wanted are skipped, not built."""
        found = {}
        while len(found) < len(wanted):
            key = self.key(self.next())
            self.expect(":")
            if key in wanted:
                found[key] = self.value()
            else:
                self.skip()
            separator = self.next().group()
            if separator == "}":
                break
            if separator != ",":
                raise PageDataError(f"Unexpected {separator!r} in the page's catalog data.")
        return found

    def key(self, token):
        if token.lastgroup == "string":
            return _string(token.group())
        if token.lastgroup in ("word", "number"):
            return token.group()
        raise PageDataError(f"Unexpected {token.group()!r} in the page's catalog data.")

    def value(self, token=None):
        token = token or self.next()
        kind, text = token.lastgroup, token.group()
        if kind == "string":
            return _string(text)
        if kind == "punct" and text == "{":
            return self.object()
        if kind == "punct" and text == "[":
            return self.array()
        if kind == "number":
            return float(text) if any(c in text for c in ".eE") else int(text)
        if kind == "ref":
            ref = int(token.group("id"))
            if token.group("assign"):
                self.refs[ref] = self.value()
            return self.refs.get(ref)
        if kind == "word" and text.startswith("void"):
            return None
        if kind == "word" and text in _WORDS:
            return _WORDS[text]
        if kind in ("word", "other") or text == "(":  # new Date(...), Object.assign(...), ...
            self.pushed_back = token
            self.skip()
            return None
        raise PageDataError(f"Unexpected {text!r} in the page's catalog data.")

    def object(self):
        result = {}
        token = self.next()
        while token.group() != "}":
            key = self.key(token)
            self.expect(":")
            result[key] = self.value()
            token = self.next()
            if token.group() == ",":
                token = self.next()
            elif token.group() != "}":
                raise PageDataError(f"Unexpected {token.group()!r} in the page's catalog data.")
        return result

    def array(self):
        items = []
        token = self.next()
        while token.group() != "]":
            items.append(self.value(token))
            token = self.next()
            if token.group() == ",":
                token = self.next()
            elif token.group() != "]":
                raise PageDataError(f"Unexpected {token.group()!r} in the page's catalog data.")
        return items

    def skip(self):
        """Pass over one value, leaving the `,` `}` or `]` after it to be read next."""
        depth = 0
        while True:
            token = self.next()
            text = token.group()
            if token.lastgroup == "punct":
                if text in _OPENERS:
                    depth += 1
                elif text in _CLOSERS:
                    if depth == 0:
                        self.pushed_back = token
                        return
                    depth -= 1
                elif text == "," and depth == 0:
                    self.pushed_back = token
                    return

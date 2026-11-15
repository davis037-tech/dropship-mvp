"""Serves the static frontend and injects server-side SEO tags (title, description,
Open Graph, canonical, Product JSON-LD) so crawlers and link previews see real content
without executing JavaScript."""
from __future__ import annotations

import html
import json
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, request, send_from_directory

from backend.models import products as product_repo

bp = Blueprint("pages", __name__)


def ctx():
    return current_app.extensions["shop"]


def _root() -> Path:
    return ctx().settings.frontend_dir


def _base() -> str:
    return ctx().settings.public_base_url or request.host_url.rstrip("/")


def _esc(v) -> str:
    return html.escape(str(v), quote=True)


def _seo(*, title, description, path, noindex=False, image=None, og_type="website", jsonld=None) -> str:
    canonical = _base() + path
    parts = [f"<title>{_esc(title)}</title>", f'<meta name="description" content="{_esc(description)}">',
             f'<link rel="canonical" href="{_esc(canonical)}">']
    if noindex:
        parts.append('<meta name="robots" content="noindex, nofollow">')
    parts += [f'<meta property="og:type" content="{og_type}">', f'<meta property="og:title" content="{_esc(title)}">',
              f'<meta property="og:description" content="{_esc(description)}">',
              f'<meta property="og:url" content="{_esc(canonical)}">',
              f'<meta property="og:site_name" content="{_esc(ctx().settings.store_name)}">',
              f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">']
    if image:
        parts.append(f'<meta property="og:image" content="{_esc(image)}">')
    if jsonld:
        safe = json.dumps(jsonld, ensure_ascii=False).replace("<", "\\u003c")
        parts.append(f'<script type="application/ld+json">{safe}</script>')
    return "\n    ".join(parts)


def _render(template: str, seo_html: str, status: int = 200) -> Response:
    text = (_root() / template).read_text(encoding="utf-8")
    text = text.replace("<!--SEO-->", seo_html).replace("{{STORE_NAME}}", _esc(ctx().settings.store_name))
    return Response(text, status=status, mimetype="text/html")


def _abs(url: str) -> str:
    return url if url.startswith("https://") else _base() + url


@bp.get("/")
def home():
    c = ctx()
    rows = [p for p in product_repo.list_all(c.db, public_only=True)]
    name = rows[0]["name"] if rows else "Enrichment feeding mats for dogs"
    desc = "A simple way to turn everyday feeding into an engaging sniff-and-search activity for dogs."
    image = None
    if rows and rows[0]["images"] and rows[0]["images"][0].get("url"):
        image = _abs(rows[0]["images"][0]["url"])
    return _render("index.html", _seo(title=f"{c.settings.store_name} | {name}", description=desc, path="/", image=image))


@bp.get("/product/<slug>")
def product_page(slug):
    c = ctx()
    p = product_repo.get_by_slug(c.db, slug)
    if p is None or p["status"] not in product_repo.PUBLIC_STATUSES:
        return _render("product.html", _seo(title=f"Product not found | {c.settings.store_name}",
                                            description="This product could not be found.", path=request.path, noindex=True), 404)
    pub = product_repo.to_public(p, c.settings.max_quantity)
    desc = (p["description"] or p["name"]).replace("\n", " ")[:155]
    image = _abs(pub["images"][0]["url"]) if pub["images"] and pub["images"][0]["url"] else None
    ld = {"@context": "https://schema.org", "@type": "Product", "name": p["name"], "description": desc,
          "url": _base() + request.path,
          "offers": {"@type": "Offer", "price": f"{pub['price']:.2f}", "priceCurrency": pub["currency"],
                     "availability": "https://schema.org/InStock" if pub["available"] else "https://schema.org/OutOfStock",
                     "url": _base() + request.path}}
    if image:
        ld["image"] = [image]
    return _render("product.html", _seo(title=f"{p['name']} | {c.settings.store_name}", description=desc,
                                        path=request.path, image=image, og_type="product", jsonld=ld))


def _private_page(template: str, title: str):
    c = ctx()
    return _render(template, _seo(title=f"{title} | {c.settings.store_name}", description=title, path=request.path, noindex=True))


@bp.get("/cart")
def cart():
    return _private_page("cart.html", "Your cart")


@bp.get("/checkout")
def checkout():
    return _private_page("checkout.html", "Checkout")


@bp.get("/order")
def order():
    return _private_page("order.html", "Order status")


@bp.get("/admin")
def admin():
    return _private_page("admin/index.html", "Admin")


@bp.get("/src/<path:filename>")
def static_src(filename):
    resp = send_from_directory(_root() / "src", filename, max_age=3600 if ctx().settings.is_production else 0)
    return resp


@bp.get("/robots.txt")
def robots():
    body = "User-agent: *\nDisallow: /admin\nDisallow: /api/\nDisallow: /dev/\nDisallow: /cart\nDisallow: /checkout\nDisallow: /order\n"
    body += f"Sitemap: {_base()}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap():
    urls = [_base() + "/"] + [f"{_base()}/product/{p['slug']}" for p in product_repo.list_all(ctx().db, public_only=True)]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "".join(f"  <url><loc>{_esc(u)}</loc></url>\n" for u in urls) + "</urlset>\n"
    return Response(xml, mimetype="application/xml")

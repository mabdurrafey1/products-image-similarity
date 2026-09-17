"""Keeps store listings as Excel workbooks that the duplicate finder reads like any other input file."""
from __future__ import annotations

import glob
import os
import re
from datetime import datetime
from typing import Optional

import pandas as pd

from ..domain import Product, StoreError, StoreListing, StoreRef

PRODUCTS_SHEET = "Products"  # first sheet, which the search pipeline reads
INFO_SHEET = "Store Info"
TIME_FORMAT = "%Y-%m-%d %H:%M"
# Column names the search pipeline and HTML report already understand
SKU, PARTNER_SKU, TITLE, BRAND, PRICE = "sku", "PartnerSKU", "Product Title", "Brand", "Price"
MAIN_IMAGE, LINK, ALL_IMAGES, ADDED_ON = "Main Image URL", "Product Link", "Combined_All_Image_URLs", "Added On"
COLUMNS = [SKU, PARTNER_SKU, TITLE, BRAND, PRICE, MAIN_IMAGE, LINK, ALL_IMAGES, ADDED_ON]


class ExcelListingRepository:
    """ListingRepository keeping one workbook per store in `directory`."""

    def __init__(self, directory: str):
        self.directory = directory

    def find(self, store: StoreRef) -> Optional[str]:
        pattern = f"Noon - * ({glob.escape(_store_tag(store))}).xlsx"
        matches = sorted(glob.glob(os.path.join(glob.escape(self.directory), pattern)))
        return matches[0] if matches else None

    def load(self, location: str) -> StoreListing:
        name = os.path.basename(location)
        info = _read_info(location)
        if not info or not info.get("Store URL"):
            raise StoreError(f"'{name}' wasn't created by Fetch Store, so it can't be refreshed.")
        try:
            rows = pd.read_excel(location, sheet_name=PRODUCTS_SHEET, dtype=str).fillna("")
            return StoreListing(
                store=StoreRef.parse(info["Store URL"]),
                name=info.get("Store Name") or name,
                products=[_to_product(row) for row in rows.to_dict("records")],
                fetched_at=datetime.strptime(info["Last Full Fetch"], TIME_FORMAT),
                refreshed_at=datetime.strptime(info["Last Refresh"], TIME_FORMAT),
            )
        except (KeyError, ValueError) as e:
            raise StoreError(f"'{name}' is damaged ({e}); fetch the store again.") from e

    def save(self, listing: StoreListing, location: Optional[str] = None) -> str:
        location = location or os.path.join(self.directory, _file_name(listing))
        folder = os.path.dirname(location) or "."
        os.makedirs(folder, exist_ok=True)
        # Write a hidden copy and swap it in, so a search reading the listing never sees half a workbook
        # (the leading dot also keeps it out of the GUI's *.xlsx list)
        temp = os.path.join(folder, f".{os.path.basename(location)}")
        try:
            with pd.ExcelWriter(temp, engine="openpyxl") as writer:
                _products_frame(listing).to_excel(writer, sheet_name=PRODUCTS_SHEET, index=False)
                _info_frame(listing).to_excel(writer, sheet_name=INFO_SHEET, index=False)
            os.replace(temp, location)
        except PermissionError as e:
            raise StoreError(f"Couldn't save '{os.path.basename(location)}'. Close it in Excel and try again.") from e
        finally:
            if os.path.exists(temp):
                os.remove(temp)
        return location

    @staticmethod
    def is_listing(location: str) -> bool:
        return _read_info(location) is not None


def _read_info(location: str) -> Optional[dict]:
    try:
        with pd.ExcelFile(location) as book:
            if INFO_SHEET not in book.sheet_names:
                return None
            frame = pd.read_excel(book, sheet_name=INFO_SHEET, dtype=str).fillna("")
        return dict(zip(frame["Field"], frame["Value"]))
    except Exception:
        return None  # unreadable or not a workbook: not a listing


def _store_tag(store: StoreRef) -> str:
    return f"{store.country} {store.path.replace('/', ' ')}"


def _file_name(listing: StoreListing) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "", listing.name).strip() or "Store"
    return f"Noon - {name} ({_store_tag(listing.store)}).xlsx"


def _products_frame(listing: StoreListing) -> pd.DataFrame:
    return pd.DataFrame([{
        SKU: p.sku,
        PARTNER_SKU: p.psku,
        TITLE: p.title,
        BRAND: p.brand,
        PRICE: p.price,
        MAIN_IMAGE: p.image_urls[0] if p.image_urls else "",
        LINK: p.link,
        ALL_IMAGES: ";".join(p.image_urls),
        ADDED_ON: p.added_on,
    } for p in listing.products], columns=COLUMNS)


def _info_frame(listing: StoreListing) -> pd.DataFrame:
    info = {
        "Store URL": listing.store.url,
        "Store Name": listing.name,
        "Products": len(listing.products),
        "Last Full Fetch": listing.fetched_at.strftime(TIME_FORMAT),
        "Last Refresh": listing.refreshed_at.strftime(TIME_FORMAT),
    }
    return pd.DataFrame(list(info.items()), columns=["Field", "Value"])


def _to_product(row: dict) -> Product:
    price = row.get(PRICE, "")
    return Product(
        sku=row[SKU].strip(),
        psku=row.get(PARTNER_SKU, "").strip(),   # blank in listings saved before it was carried through
        title=row.get(TITLE, ""),
        brand=row.get(BRAND, ""),
        price=float(price) if price else None,
        link=row.get(LINK, ""),
        image_urls=tuple(url for url in row.get(ALL_IMAGES, "").split(";") if url),
        added_on=row.get(ADDED_ON, ""),
    )

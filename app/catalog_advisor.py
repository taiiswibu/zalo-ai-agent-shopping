"""Import curated product specs linked to a registered exact-variant manufacturer code.

No claim of live stock/price is made and no fabricated URLs are generated.
"""
from pathlib import Path
import csv
from urllib.parse import urlparse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import BuyerProductSpec, CompareModel, RetailerListing
from app.price_compare import import_watchlist


def import_specs(db:Session, path: str|Path) -> tuple[int,int]:
    added=updated=0
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            key=row['model_key'].strip().upper()
            model=db.scalar(select(CompareModel).where(CompareModel.model_key==key))
            if model is None:
                raise ValueError('Import watchlist BEFORE specs: ' + key)
            url=row['evidence_url'].strip()
            # Ensure the evidence URL is EXACTLY among the registered retailer URLs.
            links=db.scalars(select(RetailerListing).where(RetailerListing.model_id==model.id)).all()
            if url not in {l.url for l in links} or urlparse(url).scheme != 'https':
                raise ValueError('Evidence URL must match registered listing: ' + key)
            vals={"category":row['category'].strip().lower(),"style":row['style'].strip().lower(),
                  "tags":[v.strip().lower() for v in row['tags'].split('|') if v.strip()],
                  "use_cases":[v.strip().lower() for v in row['use_cases'].split('|') if v.strip()],
                  "highlights":row['highlights'].strip()[:350],
                  "reference_min_vnd":int(row.get("reference_min_vnd", 0) or 0),
                  "reference_max_vnd":int(row.get("reference_max_vnd", 0) or 0),"evidence_url":url}
            if vals["reference_max_vnd"] and vals["reference_min_vnd"] > vals["reference_max_vnd"]:
                raise ValueError("Invalid approximate price band: " + key)
            spec=db.scalar(select(BuyerProductSpec).where(BuyerProductSpec.model_key==key))
            if not spec:
                db.add(BuyerProductSpec(model_key=key,**vals));added+=1
            else:
                for k,v in vals.items(): setattr(spec,k,v)
                updated+=1
    db.commit()
    return added,updated


def seed_headphones(db:Session) -> dict:
    root=Path(__file__).parent.parent/'examples'
    added,existing=import_watchlist(db,root/'real_headphone_watchlist_v4.csv')
    # Existing v3 sample: same RAM/ROM new-phone variant, three verified product page URLs.
    a,b=import_watchlist(db,root/'retailer_watchlist.csv')
    added+=a;existing+=b
    specs,upd=import_specs(db,root/'curated_headphone_specs_v4.csv')
    return {'listings_created':added,'listings_existing':existing,'specs_created':specs,'specs_updated':upd}

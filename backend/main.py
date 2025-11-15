import os
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from datetime import datetime, timezone

from database import db, create_document, get_documents
from bson import ObjectId


def serialize_id(value: Any) -> str:
    if isinstance(value, ObjectId):
        return str(value)
    return str(value)


def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    d = {**doc}
    if d.get("_id") is not None:
        d["id"] = serialize_id(d.pop("_id"))
    # convert datetime to isoformat strings
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.astimezone(timezone.utc).isoformat()
    return d


# Pydantic models
class Book(BaseModel):
    title: str
    author: str
    price: int = Field(..., ge=0)
    tagline: Optional[str] = None
    features: List[str] = []
    img: Optional[str] = None
    slug: str
    rating: float = 4.9
    review_count: int = 0


class ReviewCreate(BaseModel):
    product_id: str
    name: str
    rating: int = Field(..., ge=1, le=5)
    text: str


class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(..., ge=1)


class ShippingInfo(BaseModel):
    name: str
    phone: str
    address: str
    city: str
    postal_code: str


class OrderCreate(BaseModel):
    items: List[OrderItem]
    shipping: ShippingInfo
    notes: Optional[str] = None


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Hello from FastAPI Backend!"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "❌ Not Set",
        "database_name": "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# Products
@app.get("/api/products")
def list_products(limit: int = Query(50, ge=1, le=200)):
    products = get_documents("book", {}, limit)
    return [serialize_doc(p) for p in products]


@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    try:
        doc = db["book"].find_one({"_id": ObjectId(product_id)})
    except Exception:
        doc = db["book"].find_one({"slug": product_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    return serialize_doc(doc)


# Reviews
@app.get("/api/reviews")
def get_reviews(product_id: str, limit: int = Query(20, ge=1, le=100)):
    try:
        filter_q = {"product_id": str(ObjectId(product_id))}
    except Exception:
        filter_q = {"product_id": product_id}
    reviews = get_documents("review", filter_q, limit)
    return [serialize_doc(r) for r in reviews]


@app.post("/api/reviews")
def create_review(payload: ReviewCreate):
    data = {
        "product_id": payload.product_id,
        "name": payload.name,
        "rating": payload.rating,
        "text": payload.text,
    }
    _id = create_document("review", data)
    # Update product counters
    try:
        db["book"].update_one(
            {"_id": ObjectId(payload.product_id) if ObjectId.is_valid(payload.product_id) else payload.product_id},
            [{
                "$set": {
                    "review_count": {"$add": ["$review_count", 1]},
                    "rating": {
                        "$cond": [
                            {"$gt": ["$review_count", 0]},
                            {"$divide": [{"$add": [{"$multiply": ["$rating", "$review_count"]}, payload.rating]}, {"$add": ["$review_count", 1]}]},
                            payload.rating
                        ]
                    }
                }
            }]
        )
    except Exception:
        pass
    return {"id": _id}


# Orders
@app.post("/api/orders")
def create_order(order: OrderCreate):
    # Basic total calculation (server-side trust should be combined with product prices)
    total = 0
    items_serialized: List[Dict[str, Any]] = []
    for it in order.items:
        # fetch product
        prod = None
        try:
            prod = db["book"].find_one({"_id": ObjectId(it.product_id)})
        except Exception:
            prod = db["book"].find_one({"slug": it.product_id})
        if not prod:
            raise HTTPException(status_code=400, detail=f"Invalid product {it.product_id}")
        price = int(prod.get("price", 0))
        subtotal = price * it.quantity
        total += subtotal
        items_serialized.append({
            "product_id": serialize_id(prod.get("_id")),
            "title": prod.get("title"),
            "quantity": it.quantity,
            "price": price,
            "subtotal": subtotal,
        })
    data = {
        "items": items_serialized,
        "shipping": order.shipping.model_dump(),
        "notes": order.notes,
        "status": "created",
        "total": total,
        "currency": "IDR",
    }
    _id = create_document("order", data)
    return {"order_id": _id, "total": total}


# Seed endpoint to populate initial books
@app.post("/api/seed")
def seed_data():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    initial: List[Book] = [
        Book(
            title="Sebelum Aku Tiada",
            author="Asma Nadia",
            price=89000,
            tagline="Surat-Surat dari Gaza — Kisah Haru yang Menggugah Jiwa",
            features=["100% Royalti untuk Palestina", "Kisah nyata dari anak-anak Gaza", "Dibaca oleh lebih dari 50.000 orang"],
            img="https://images.unsplash.com/photo-1544716278-ca5e3f4abd8c?q=80&w=1200&auto=format&fit=crop",
            slug="sebelum-aku-tiada",
        ),
        Book(
            title="Melawan Kemustahilan",
            author="Dewa Eka Prayoga",
            price=75000,
            tagline="Menguji Keimanan, Menjemput Keajaiban",
            features=["Best Seller Edisi Revisi", "Kisah nyata perjuangan hidup", "Highly Recommended oleh para motivator"],
            img="https://images.unsplash.com/photo-1519681393784-d120267933ba?q=80&w=1200&auto=format&fit=crop",
            slug="melawan-kemustahilan",
        ),
        Book(
            title="Titik Balik",
            author="Arafat",
            price=69000,
            tagline="Ada 365 Hari dalam Setahun, Manakah yang Akan Jadi Titik Balik Dirimu?",
            features=["Buku harian reflektif", "Cocok untuk pencari makna dan transformasi diri", "Desain cover estetik, cocok untuk koleksi"],
            img="https://images.unsplash.com/photo-1526318472351-c75fcf070305?q=80&w=1200&auto=format&fit=crop",
            slug="titik-balik",
        ),
    ]

    inserted = 0
    for b in initial:
        exists = db["book"].find_one({"slug": b.slug})
        if not exists:
            create_document("book", b)
            inserted += 1
    return {"inserted": inserted}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

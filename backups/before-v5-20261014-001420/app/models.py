from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, UniqueConstraint, Index, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    category: Mapped[str] = mapped_column(String(100), default="phu-kien")
    description: Mapped[str] = mapped_column(Text, default="")
    price_vnd: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    warranty_months: Mapped[int] = mapped_column(Integer, default=0)
    source_url: Mapped[str] = mapped_column(Text, default="")
    is_authoritative: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class Knowledge(Base):
    __tablename__ = "knowledge"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, default="")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ChatEvent(Base):
    __tablename__ = "chat_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    query: Mapped[str] = mapped_column(Text, default="")
    reply: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="processing")
    error: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True)
    chat_id: Mapped[str] = mapped_column(String(128), index=True)
    sku: Mapped[str] = mapped_column(String(80), ForeignKey("products.sku"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_vnd: Mapped[int] = mapped_column(Integer)
    total_vnd: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

# v2 additions. Existing tables/rows remain untouched by Base.metadata.create_all().
class RagChunk(Base):
    __tablename__ = "rag_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_id: Mapped[int] = mapped_column(Integer, ForeignKey("knowledge.id"), nullable=False, index=True)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("knowledge_id", "chunk_no", name="uq_rag_doc_chunk"),)

class RagVector(Base):
    __tablename__ = "rag_vectors"
    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int] = mapped_column(Integer, ForeignKey("rag_chunks.id"), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    vector: Mapped[list] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("chunk_id", "model", name="uq_rag_chunk_model"),)

class AgentTrace(Base):
    __tablename__ = "agent_traces"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    intent: Mapped[str] = mapped_column(String(50), nullable=False)
    retrieval_keys: Mapped[list] = mapped_column(JSON, default=list)
    engine: Mapped[str] = mapped_column(String(32), default="fallback")
    llm_provider: Mapped[str] = mapped_column(String(40), default="none")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ChatContext(Base):
    __tablename__ = "chat_contexts"
    chat_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_product_query: Mapped[str] = mapped_column(Text, default="")
    last_product_skus: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class CrawlObservation(Base):
    __tablename__ = "crawl_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("source_url", "source_hash", name="uq_crawl_observation"),)

# v3: external retailer listings are observations, NOT shop-owned sale inventory.
class CompareModel(Base):
    __tablename__ = "compare_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    model_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    brand: Mapped[str] = mapped_column(String(90), nullable=False)
    model_token: Mapped[str] = mapped_column(String(80), nullable=False)
    ram_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    condition: Mapped[str] = mapped_column(String(15), nullable=False, default="new")

class RetailerListing(Base):
    __tablename__ = "retailer_listings"
    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(Integer, ForeignKey("compare_models.id"), nullable=False, index=True)
    retailer: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_status: Mapped[str] = mapped_column(String(40), default="never_checked")
    last_error: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class RetailerPriceSnapshot(Base):
    __tablename__ = "retailer_price_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("retailer_listings.id"), nullable=False, index=True)
    price_vnd: Mapped[int] = mapped_column(Integer, nullable=False)
    page_product_name: Mapped[str] = mapped_column(String(250), nullable=False)
    method: Mapped[str] = mapped_column(String(40), nullable=False)  # jsonld / meta / manual_review
    condition: Mapped[str] = mapped_column(String(15), default="new")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_retailer_price_snapshot_listing_time", "listing_id", "observed_at"),)

# v4 additive tables. Never modify the pre-existing customers/orders or v3 snapshots.
class BuyerSession(Base):
    __tablename__ = "buyer_sessions"
    chat_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    category: Mapped[str] = mapped_column(String(50), default="")
    budget_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    style: Mapped[str] = mapped_column(String(30), default="")
    use_case: Mapped[str] = mapped_column(String(30), default="")
    priority: Mapped[str] = mapped_column(String(30), default="")
    device: Mapped[str] = mapped_column(String(30), default="")
    stage: Mapped[str] = mapped_column(String(30), default="budget")
    shortlisted_keys: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class BuyerProductSpec(Base):
    __tablename__ = "buyer_product_specs"
    id: Mapped[int] = mapped_column(primary_key=True)
    model_key: Mapped[str] = mapped_column(String(100), ForeignKey("compare_models.model_key"), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    style: Mapped[str] = mapped_column(String(30), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    use_cases: Mapped[list] = mapped_column(JSON, default=list)
    highlights: Mapped[str] = mapped_column(Text, default="")
    reference_min_vnd: Mapped[int] = mapped_column(Integer, default=0)  # approximate market tier, NOT a current offer
    reference_max_vnd: Mapped[int] = mapped_column(Integer, default=0)
    evidence_url: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

# v5 audit: one record per executed model-selected read-only tool.
class AgentToolTrace(Base):
    __tablename__ = "agent_tool_traces"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(70), nullable=False)
    result_intent: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

"""
Proviso — Terms. Spend. Proof.
ETL Pipeline — Prefect Flow
==============================
Monitors contract folder, extracts text from PDFs, splits into
parent-child chunks, embeds with HuggingFace, stores in ChromaDB.

Usage:
    python scripts/etl_pipeline.py          # Run once
    prefect server start                    # (optional) Start Prefect UI
    python scripts/etl_pipeline.py --serve  # Register as scheduled flow
"""

import os, sys, hashlib, json, tempfile
from pathlib import Path
from datetime import timedelta

PROJECT_ROOT = Path(__file__).parent.parent
# Prefect's ephemeral server needs its own SQLite database. A OneDrive-backed
# workspace with a Unicode path can prevent that server from opening the file
# on Windows, so use a local, ASCII-safe application-data directory by default.
# PROVISO_PREFECT_HOME can override this location for managed environments.
_PREFECT_DEFAULT_HOME = Path(os.getenv("LOCALAPPDATA", tempfile.gettempdir())) / "proviso_prefect"
PREFECT_HOME = Path(os.getenv("PROVISO_PREFECT_HOME", str(_PREFECT_DEFAULT_HOME)))
PREFECT_HOME.mkdir(parents=True, exist_ok=True)
os.environ["PREFECT_HOME"] = str(PREFECT_HOME)
os.environ["PREFECT_API_DATABASE_CONNECTION_URL"] = (
    f"sqlite+aiosqlite:///{(PREFECT_HOME / 'prefect.db').as_posix()}"
)
# The ETL is a local-only demonstration workflow; disable Prefect's optional
# server analytics to avoid network telemetry and SQLite heartbeat contention.
os.environ["PREFECT_SERVER_ANALYTICS_ENABLED"] = "false"
# The embedding model is sizeable; store its cache alongside the project so
# local runs remain reproducible and do not depend on a writable user profile.
os.environ["HF_HOME"] = str(PROJECT_ROOT / ".cache" / "huggingface")

# Prefect
from prefect import flow, task, get_run_logger
from prefect.cache_policies import NO_CACHE

# PDF extraction
import fitz  # PyMuPDF

# Text splitting
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Embeddings
from langchain_huggingface import HuggingFaceEmbeddings

# Vector store
import chromadb

# ── Configuration ──────────────────────────────────────────────────
DATA_DIR = PROJECT_ROOT / "data"
CONTRACTS_DIR = DATA_DIR / "contracts"
METADATA_FILE = DATA_DIR / "contracts_metadata.json"
PROCESSED_LOG = DATA_DIR / ".processed_contracts.json"
INDEX_VERSION = "cosine-v1"

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8100"))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "vendor_contracts")

# Use local ChromaDB if server not available
USE_LOCAL_CHROMA = os.getenv("USE_LOCAL_CHROMA", "true").lower() == "true"
LOCAL_CHROMA_DIR = str(DATA_DIR / "chroma_db")

EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"

# Parent-child chunk configuration
CHILD_CHUNK_SIZE = 500
CHILD_CHUNK_OVERLAP = 100
PARENT_CHUNK_SIZE = 1500
PARENT_CHUNK_OVERLAP = 200


def _file_hash(filepath: Path) -> str:
    """Compute MD5 hash of file for change detection."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_processed_log() -> dict:
    if PROCESSED_LOG.exists():
        with open(PROCESSED_LOG) as f:
            processed = json.load(f)
        # A distance-metric change requires a full rebuild: a previously
        # indexed L2 collection cannot be converted in place to cosine.
        if processed.get("_index_version") == INDEX_VERSION:
            return processed
    return {}


def _save_processed_log(log: dict):
    log = {**log, "_index_version": INDEX_VERSION}
    with open(PROCESSED_LOG, "w") as f:
        json.dump(log, f, indent=2)


@task(name="scan_contracts", retries=2)
def scan_contracts() -> list[dict]:
    """Scan contract folder for new or modified PDFs."""
    logger = get_run_logger()
    processed = _load_processed_log()
    new_files = []

    if not CONTRACTS_DIR.exists():
        logger.warning(f"Contracts directory not found: {CONTRACTS_DIR}")
        return []

    # Load vendor metadata for enrichment
    vendor_meta = {}
    if METADATA_FILE.exists():
        with open(METADATA_FILE) as f:
            for item in json.load(f):
                vendor_meta[item["filename"]] = item

    for pdf_path in sorted(CONTRACTS_DIR.glob("*.pdf")):
        file_hash = _file_hash(pdf_path)
        if pdf_path.name in processed and processed[pdf_path.name] == file_hash:
            continue  # Already processed, no changes

        meta = vendor_meta.get(pdf_path.name, {})
        new_files.append({
            "path": str(pdf_path),
            "filename": pdf_path.name,
            "hash": file_hash,
            "vendor_name": meta.get("vendor_name", "Unknown"),
            "vendor_id": meta.get("vendor_id", 0),
            "contract_type": meta.get("contract_type", "unknown"),
            "contract_number": meta.get("contract_number", "Unknown"),
            "effective_date": meta.get("effective_date"),
            "expiry_date": meta.get("expiry_date"),
            "monthly_cap": meta.get("monthly_cap"),
        })

    logger.info(f"Found {len(new_files)} new/modified contracts to process")
    return new_files


@task(name="extract_text", retries=1)
def extract_text(file_info: dict) -> dict:
    """Extract text from PDF with page-level metadata."""
    logger = get_run_logger()
    pdf_path = file_info["path"]

    pages = []
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            if text.strip():
                pages.append({
                    "text": text,
                    "page_number": page_num + 1,
                    "filename": file_info["filename"],
                    "vendor_name": file_info["vendor_name"],
                    "vendor_id": file_info["vendor_id"],
                    "contract_type": file_info["contract_type"],
                    "contract_number": file_info["contract_number"],
                    "effective_date": file_info["effective_date"],
                    "expiry_date": file_info["expiry_date"],
                    "monthly_cap": file_info["monthly_cap"],
                })
        doc.close()
    except Exception as e:
        logger.error(f"Failed to extract {pdf_path}: {e}")
        return {"file_info": file_info, "pages": []}

    logger.info(f"  Extracted {len(pages)} pages from {file_info['filename']}")
    return {"file_info": file_info, "pages": pages}


@task(name="chunk_and_embed")
def chunk_and_embed(extraction: dict, embeddings_model) -> dict:
    """Split text into parent-child chunks and generate embeddings."""
    logger = get_run_logger()
    pages = extraction["pages"]
    if not pages:
        return {"chunks": [], "embeddings": [], "metadatas": [], "ids": []}

    # Parent splitter (larger context chunks)
    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=PARENT_CHUNK_SIZE,
        chunk_overlap=PARENT_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ".", " "],
    )
    # Child splitter (precise retrieval chunks)
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHILD_CHUNK_SIZE,
        chunk_overlap=CHILD_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ".", " "],
    )

    all_chunks = []
    all_metadatas = []
    all_ids = []

    filename = pages[0]["filename"]
    vendor = pages[0]["vendor_name"]
    vendor_id = pages[0]["vendor_id"]

    for page_data in pages:
        page_text = page_data["text"]
        page_num = page_data["page_number"]

        # Create parent chunks
        parent_chunks = parent_splitter.split_text(page_text)

        for pi, parent_chunk in enumerate(parent_chunks):
            # Create child chunks from parent
            child_chunks = child_splitter.split_text(parent_chunk)

            for ci, child_chunk in enumerate(child_chunks):
                chunk_id = f"{filename}_p{page_num}_par{pi}_ch{ci}"
                all_chunks.append(child_chunk)
                all_ids.append(chunk_id)
                all_metadatas.append({
                    "filename": filename,
                    "vendor_name": vendor,
                    "vendor_id": str(vendor_id),
                    "page_number": page_num,
                    "chunk_type": "child",
                    "parent_text": parent_chunk[:500],  # Store truncated parent for context
                    "contract_type": page_data["contract_type"],
                    "contract_number": page_data["contract_number"],
                    "effective_date": page_data["effective_date"] or "",
                    "expiry_date": page_data["expiry_date"] or "",
                    "monthly_cap_inr": int(page_data["monthly_cap"] or 0),
                })

    # Generate embeddings in batch
    if all_chunks:
        all_embeddings = embeddings_model.embed_documents(all_chunks)
    else:
        all_embeddings = []

    logger.info(f"  Created {len(all_chunks)} chunks from {filename}")
    return {
        "chunks": all_chunks,
        "embeddings": all_embeddings,
        "metadatas": all_metadatas,
        "ids": all_ids,
        "file_info": extraction["file_info"],
    }


@task(name="store_vectors", retries=2, cache_policy=NO_CACHE)
def store_vectors(chunk_data: dict, collection) -> str:
    """Upsert chunks into ChromaDB collection."""
    logger = get_run_logger()
    if not chunk_data["chunks"]:
        return "skipped"

    # ChromaDB upsert (handles both insert and update)
    batch_size = 100
    chunks = chunk_data["chunks"]
    for i in range(0, len(chunks), batch_size):
        end = min(i + batch_size, len(chunks))
        collection.upsert(
            documents=chunks[i:end],
            embeddings=chunk_data["embeddings"][i:end],
            metadatas=chunk_data["metadatas"][i:end],
            ids=chunk_data["ids"][i:end],
        )

    logger.info(f"  Stored {len(chunks)} vectors for {chunk_data['file_info']['filename']}")
    return chunk_data["file_info"]["filename"]


@flow(name="contract_etl_pipeline", log_prints=True)
def contract_etl_pipeline():
    """Main ETL flow: scan -> extract -> chunk -> embed -> store."""
    logger = get_run_logger()
    logger.info("=" * 60)
    logger.info("  Contract ETL Pipeline Starting")
    logger.info("=" * 60)

    # 1. Scan for new contracts
    new_files = scan_contracts()
    if not new_files:
        logger.info("No new contracts to process. Pipeline complete.")
        return

    # 2. Initialize embedding model (shared across all files)
    logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
    embeddings_model = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    # 3. Initialize ChromaDB
    if USE_LOCAL_CHROMA:
        logger.info(f"Using local ChromaDB: {LOCAL_CHROMA_DIR}")
        chroma_client = chromadb.PersistentClient(path=LOCAL_CHROMA_DIR)
    else:
        logger.info(f"Connecting to ChromaDB: {CHROMA_HOST}:{CHROMA_PORT}")
        chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)

    collection = chroma_client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={
            "description": "Enterprise vendor contract chunks",
            "hnsw:space": "cosine",
        },
    )
    if (collection.metadata or {}).get("hnsw:space") != "cosine":
        logger.warning(
            "Rebuilding the local Chroma collection with cosine distance for "
            "normalized embeddings."
        )
        chroma_client.delete_collection(CHROMA_COLLECTION)
        collection = chroma_client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={
                "description": "Enterprise vendor contract chunks",
                "hnsw:space": "cosine",
            },
        )

    # 4. Process each file
    processed_log = _load_processed_log()
    processed_count = 0

    for file_info in new_files:
        logger.info(f"Processing: {file_info['filename']}")

        # Extract
        extraction = extract_text(file_info)

        # Chunk & embed
        chunk_data = chunk_and_embed(extraction, embeddings_model)

        # Store
        result = store_vectors(chunk_data, collection)

        if result != "skipped":
            processed_log[file_info["filename"]] = file_info["hash"]
            processed_count += 1

    # Save processed log
    _save_processed_log(processed_log)

    # Summary
    total_vectors = collection.count()
    logger.info("=" * 60)
    logger.info(f"  Pipeline Complete!")
    logger.info(f"  Processed: {processed_count} contracts")
    logger.info(f"  Total vectors in collection: {total_vectors}")
    logger.info("=" * 60)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        # Register as a scheduled deployment
        contract_etl_pipeline.serve(
            name="daily-contract-etl",
            interval=timedelta(hours=24),
        )
    else:
        contract_etl_pipeline()

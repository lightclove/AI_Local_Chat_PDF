"""
Модуль: RAG — индексация PDF и извлечение контекста

Зачем:
- Давать модели релевантный контекст из пользовательских документов, чтобы улучшить качество ответов.

Почему именно так:
- Храним эмбеддинги в ChromaDB для локальной и быстрой похожести без внешних зависимостей.
- `pdfplumber` — быстрый путь для извлечения текстового слоя; OCR — только как запасной вариант,
  чтобы не требовать OCR для «нормальных» PDF.
- Простое разбиение на чанки с перекрытием — компромисс между полнотой контекста и скоростью.

Когда использовать:
- При формировании системного промпта для LLM: именно отсюда берётся контекст по запросу пользователя.

Основные обязанности:
- Обработать PDF → извлечь текст → разбить на чанки → получить эмбеддинги → положить в векторное хранилище.
- По запросу пользователя найти наиболее близкие фрагменты и собрать их в читаемый контекст.

Вне зоны ответственности:
- Генерация ответов и форматирование разговора (это в веб-слое/клиенте LLM).
- Долгосрочное хранение исходных файлов — модуль оперирует путями, но не управляет их жизненным циклом.

Ключевые решения/компромиссы:
- Ленивая загрузка `SentenceTransformer` уменьшает время старта при отсутствии загрузок/поисков.
- Фильтруем пустые/слишком короткие чанки и проверяем консистентность размеров эмбеддингов.
- OCR включаем только при полном отсутствии текстового слоя — экономия ресурсов.

Надёжность:
- Возвращаем пустые результаты вместо исключений при поиске, чтобы не ломать основной поток ответа.
"""

import os
from typing import Any

import chromadb
import pdfplumber
from chromadb.config import Settings
from loguru import logger
from sentence_transformers import SentenceTransformer

from config import RAGConfig


class DocumentProcessor:
    """Обработка PDF: извлечение текста и подготовка чанков для индексации."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200, ocr_dpi: int = 200):
        logger.debug("Initializing DocumentProcessor")
        self.chunk_size = chunk_size
        logger.debug(f"Set chunk_size to {chunk_size}")
        self.chunk_overlap = chunk_overlap
        logger.debug(f"Set chunk_overlap to {chunk_overlap}")
        self.ocr_dpi = ocr_dpi
        logger.debug(f"Set OCR DPI to {ocr_dpi}")

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        logger.debug(f"Extracting text from {pdf_path}")
        try:
            with pdfplumber.open(pdf_path) as pdf:
                logger.debug("PDF opened")
                text = ""
                for page in pdf.pages:
                    logger.debug("Processing page")
                    text += page.extract_text() or ""
                logger.debug("Text extraction complete")
                if text and text.strip():
                    return text
        except Exception as e:
            logger.error(f"Error extracting text from {pdf_path}: {e}")
            raise

        # Fallback to OCR if no text extracted
        logger.warning(f"No text extracted via pdfplumber for {pdf_path}, attempting OCR fallback")
        ocr_text = ""
        try:
            # Import optional deps lazily
            try:
                from pdf2image import convert_from_path
            except Exception as _:
                logger.warning(
                    "pdf2image not available; install pdf2image and poppler for OCR fallback"
                )
                return ""
            try:
                import pytesseract
            except Exception as _:
                logger.warning(
                    "pytesseract not available; install pytesseract and tesseract-ocr for OCR fallback"
                )
                return ""

            import os

            poppler_path = os.getenv("POPPLER_PATH", None)
            images = (
                convert_from_path(pdf_path, dpi=self.ocr_dpi, poppler_path=poppler_path)
                if poppler_path
                else convert_from_path(pdf_path, dpi=self.ocr_dpi)
            )
            for img in images:
                ocr_text += pytesseract.image_to_string(img) or ""
            if ocr_text and ocr_text.strip():
                logger.info("OCR fallback extracted text successfully")
                return ocr_text
            logger.warning("OCR fallback did not extract any text")
            return ""
        except Exception as e:
            logger.error(f"OCR fallback failed for {pdf_path}: {e}")
            raise

    def split_text_into_chunks(self, text: str) -> list[str]:
        """Разделить текст на перекрывающиеся чанки; отфильтровать слишком короткие."""
        if not text or not text.strip():
            return []

        chunks: list[str] = []
        words = text.split()
        if not words:
            return []

        current_chunk: list[str] = []
        for word in words:
            current_chunk.append(word)
            if len(current_chunk) >= self.chunk_size:
                chunk = " ".join(current_chunk).strip()
                if len(chunk) >= max(20, int(self.chunk_size * 0.2)):
                    chunks.append(chunk)
                # Move back for overlap
                overlap_words = current_chunk[-self.chunk_overlap :]
                current_chunk = overlap_words

        # Add the last chunk if meaningful
        if current_chunk:
            last = " ".join(current_chunk).strip()
            if len(last) >= 20:
                chunks.append(last)

        return chunks


class VectorStore:
    """Векторное хранилище ChromaDB для эмбеддингов документов."""

    def __init__(self, config: RAGConfig):
        self.config = config
        self._model: SentenceTransformer | None = None
        self.client = chromadb.PersistentClient(
            path=config.chroma_db_path, settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(
            name=config.collection_name,
            metadata={"description": "Document embeddings for RAG"},
        )
        self.processor = DocumentProcessor(
            config.chunk_size, config.chunk_overlap, ocr_dpi=config.ocr_dpi
        )

    def _ensure_model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self.config.embedding_model)
        return self._model

    def add_document(self, document_id: str, pdf_path: str, metadata: dict | None = None) -> None:
        """Добавить PDF-документ в векторное хранилище."""
        try:
            # Извлекаем текст из PDF
            text = self.processor.extract_text_from_pdf(pdf_path)
            if not text or not text.strip():
                logger.warning(f"No extractable text in PDF: {pdf_path}")
                raise ValueError("No extractable text found in PDF")

            chunks = self.processor.split_text_into_chunks(text)
            if not chunks:
                logger.warning(f"No meaningful chunks produced for: {pdf_path}")
                raise ValueError("No textual content to index from PDF")

            # Генерируем эмбеддинги для каждого чанка
            model = self._ensure_model()
            embeddings = model.encode(chunks)
            if getattr(embeddings, "shape", None) is not None and embeddings.shape[0] == 0:
                raise ValueError("Embedding model returned no vectors for chunks")
            if isinstance(embeddings, list) and len(embeddings) == 0:
                raise ValueError("Embedding model returned no vectors for chunks")

            # Добавляем эмбеддинги и тексты в коллекцию
            metas: list[dict[str, str]] = []
            base_raw = metadata or {"source": pdf_path, "document_id": document_id}
            base_meta: dict[str, str] = {str(k): str(v) for k, v in base_raw.items()}
            for _ in chunks:
                metas.append(dict(base_meta))

            emb_list = embeddings.tolist() if hasattr(embeddings, "tolist") else embeddings
            if not emb_list or len(emb_list) != len(chunks):
                raise ValueError("Embeddings/chunks length mismatch")

            self.collection.add(
                embeddings=emb_list,
                documents=chunks,
                metadatas=metas,
                ids=[f"{document_id}_{i}" for i in range(len(chunks))],
            )

            logger.info(f"Added {len(chunks)} chunks from document {document_id}")

        except Exception as e:
            logger.error(f"Error adding document {document_id}: {e}")
            raise

    def search_similar_documents(
        self, query: str, n_results: int | None = None
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Найти похожие фрагменты по запросу пользователя."""
        try:
            n_results = n_results or self.config.search_results
            # Получаем эмбеддинг запроса
            model = self._ensure_model()
            query_embedding = model.encode([query])

            # Ищем по коллекции
            results = self.collection.query(
                query_embeddings=query_embedding.tolist(), n_results=n_results
            )

            # Обрабатываем результаты и нормализуем к списку
            similar_docs = []
            documents = results.get("documents")
            if documents is None or not documents:
                return []
            distances = results.get("distances")
            if distances is None or not distances:
                return []
            metadatas = results.get("metadatas")
            if metadatas is None or not metadatas:
                return []
            for doc, distance, metadata in zip(
                documents[0], distances[0], metadatas[0], strict=False
            ):
                similar_docs.append((doc, distance, dict(metadata or {})))

            return similar_docs

        except Exception as e:
            logger.error(f"Error searching documents: {e}")
            return []

    def delete_document(self, document_id: str) -> None:
        """Удалить документ из векторного хранилища (по всем чанкам)."""
        try:
            # Find all document chunks
            results = self.collection.get(where={"document_id": document_id})

            if results["ids"]:
                self.collection.delete(ids=results["ids"])
                logger.info(f"Deleted document {document_id}")

        except Exception as e:
            logger.error(f"Error deleting document {document_id}: {e}")
            raise

    def get_document_count(self) -> int:
        """Количество документов в векторном хранилище (агрегировано Chroma)."""
        try:
            return self.collection.count()
        except Exception as e:
            logger.error(f"Error getting document count: {e}")
            return 0


class RAGSystem:
    """Высокоуровневый фасад RAG: индексация, поиск, удаление, список документов."""

    def __init__(self, config: RAGConfig):
        self.config = config
        self.vector_store = VectorStore(config)

    def add_pdf_document(
        self, document_id: str, pdf_path: str, metadata: dict | None = None
    ) -> None:
        """Добавить PDF-документ в RAG (с проверкой существования файла)."""
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        self.vector_store.add_document(document_id, pdf_path, metadata)

    def retrieve_relevant_context(self, query: str, n_results: int | None = None) -> str:
        """Вернуть релевантный контекст для запроса пользователя."""
        similar_docs = self.vector_store.search_similar_documents(query, n_results)

        if not similar_docs:
            return ""

        # Формируем читабельный контекст для системного промпта
        context_parts = []
        for doc, distance, metadata in similar_docs:
            context_parts.append(f"Document: {metadata.get('source', 'Unknown')}")
            context_parts.append(f"Relevance: {distance:.4f}")
            context_parts.append(f"Content: {doc}")
            context_parts.append("-" * 50)

        return "\n".join(context_parts)

    def delete_document(self, document_id: str) -> None:
        """Удалить документ из RAG (проксируем в VectorStore)."""
        self.vector_store.delete_document(document_id)

    def list_documents(self) -> list[dict]:
        """Список документов в коллекции Chroma (группировка по document_id)."""
        try:
            results = self.vector_store.collection.get()

            metadatas = results.get("metadatas")
            if metadatas is None:
                return []

            # Group by document_id
            documents: dict[str, dict[str, Any]] = {}
            for _i, metadata in enumerate(metadatas):
                doc_id = str(metadata.get("document_id", "unknown"))
                if doc_id not in documents:
                    documents[doc_id] = {
                        "document_id": doc_id,
                        "source": str(metadata.get("source", "unknown")),
                        "chunks": 0,
                    }
                documents[doc_id]["chunks"] = int(documents[doc_id]["chunks"]) + 1

            return list(documents.values())
        except Exception as e:
            logger.error(f"Error listing documents: {e}")
            return []

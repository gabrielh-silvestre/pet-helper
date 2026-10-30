import re
from dataclasses import dataclass
from pathlib import Path

import bm25s
import Stemmer  # pyrefly: ignore[missing-import]

from pet_helper.text import fold_text

_H1_PATTERN = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_STOPWORDS = sorted({fold_text(word) for word in bm25s.stopwords.STOPWORDS_PORTUGUESE})
_STEMMER = Stemmer.Stemmer("portuguese")


@dataclass(frozen=True)
class PolicyHit:
    source: str
    title: str
    text: str
    score: float


@dataclass(frozen=True)
class _PolicyDocument:
    source: str
    title: str
    text: str


def _extract_title(text: str, fallback: str) -> str:
    match = _H1_PATTERN.search(text)
    return match.group(1).strip() if match else fallback


def _load_document(path: Path) -> _PolicyDocument:
    text = path.read_text(encoding="utf-8")
    return _PolicyDocument(
        source=path.name,
        title=_extract_title(text, path.stem),
        text=text,
    )


class PolicyIndex:
    def __init__(self, documents: list[_PolicyDocument], retriever: bm25s.BM25):
        self._documents = documents
        self._retriever = retriever

    @classmethod
    def load(cls, directory: Path) -> "PolicyIndex":
        documents = [_load_document(path) for path in sorted(directory.glob("*.md"))]
        corpus_tokens = bm25s.tokenize(
            [fold_text(doc.text) for doc in documents],
            stopwords=_STOPWORDS,
            stemmer=_STEMMER,
            show_progress=False,
        )
        retriever = bm25s.BM25(method="lucene")
        retriever.index(corpus_tokens, show_progress=False)
        return cls(documents, retriever)

    def search(self, query: str, k: int = 2) -> list[PolicyHit]:
        query_tokens = bm25s.tokenize(
            [fold_text(query)],
            stopwords=_STOPWORDS,
            stemmer=_STEMMER,
            show_progress=False,
        )
        doc_ids, scores = self._retriever.retrieve(
            query_tokens,
            k=min(k, len(self._documents)),
            show_progress=False,
        )
        return [
            PolicyHit(
                source=self._documents[int(doc_id)].source,
                title=self._documents[int(doc_id)].title,
                text=self._documents[int(doc_id)].text,
                score=float(score),
            )
            for doc_id, score in zip(doc_ids[0], scores[0], strict=True)
            if score > 0
        ]

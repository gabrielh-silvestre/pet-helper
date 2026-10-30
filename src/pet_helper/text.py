import unicodedata

import bm25s


def fold_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).lower()


PORTUGUESE_STOPWORDS = sorted(
    {fold_text(word) for word in bm25s.stopwords.STOPWORDS_PORTUGUESE}
)

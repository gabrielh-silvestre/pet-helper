from pet_helper import text


def test_it_should_expose_portuguese_stopwords_folded_like_the_text_it_filters():
    stopwords = set(text.PORTUGUESE_STOPWORDS)

    assert {"para", "de", "uma", "meu"} <= stopwords
    assert all(word == text.fold_text(word) for word in stopwords)

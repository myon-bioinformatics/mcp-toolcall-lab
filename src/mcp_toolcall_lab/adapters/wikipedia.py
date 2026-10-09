"""Thin API adapter retaining the existing Wikipedia cache and result contract."""
import re


def article(title, lang='en'):
    if not isinstance(title, str) or not title.strip() or not isinstance(lang, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', lang):
        raise ValueError('invalid Wikipedia title or language')
    from ..wikipedia_tool import fetch_wikipedia_article
    return fetch_wikipedia_article(title, lang=lang)

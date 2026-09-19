# Vendored `markdown.py`

Pinned snapshot of [`myon-bioinformatics/markdown`](https://github.com/myon-bioinformatics/markdown)
`markdown.py` (stdlib helpers, not a CommonMark engine). The stub front loads
this file by path so the Pages/Docker image does not grow a pip dependency.

Refresh:

```bash
gh api repos/myon-bioinformatics/markdown/contents/markdown.py --jq .content \
  | base64 -d > vendor/markdown.py
```

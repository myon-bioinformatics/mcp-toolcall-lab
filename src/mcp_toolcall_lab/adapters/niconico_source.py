"""Attach a trusted Snapshot API adapter to source access without new transport.

Pass the adapter module transferred in PR #108, or a compatible offline double.
No dynamic import or arbitrary callable is accepted through request JSON.
"""


def register_niconico(registry, adapter):
    def search(*, q, sort, context, user_agent, targets=None, limit=100,
               offset=0, filters=None, max_pages=None):
        return adapter.paged_search(
            q=q, sort=sort, context=context, user_agent=user_agent,
            targets=targets, limit=limit, offset=offset, filters=filters,
            max_pages=max_pages,
        )

    registry.register('niconico', 'search', search, network=True)
    return registry

"""Meta-eval's cluster auto-resolution is gated and scoped.

A `retrieval` cluster means the content EXISTS and did not surface; asking "does
the KB cover this?" closed every such ticket on the first pass. And an admin who
left auto-retire off did not ask for tickets to be closed automatically either.
"""

from __future__ import annotations

import inspect

from improvement_loop import meta_eval
from improvement_loop.meta_eval import should_auto_resolve


class _Cfg:
    def __init__(self, on):
        self.meta_retire_enabled = on


def test_auto_resolve_requires_the_retire_switch_and_a_real_run():
    assert should_auto_resolve(_Cfg(True), dry_run=False)
    assert not should_auto_resolve(_Cfg(False), dry_run=False)
    assert not should_auto_resolve(_Cfg(True), dry_run=True)
    assert not should_auto_resolve(None, dry_run=False)


def test_only_content_gap_clusters_are_auto_resolvable():
    src = inspect.getsource(meta_eval.resolve_covered_clusters)
    assert 'LoopCluster.route == "content_gap"' in src
    assert '"retrieval"' not in src.split("async with")[1].split("resolved: list")[0]

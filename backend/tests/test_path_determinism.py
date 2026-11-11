"""Equal-cost path selection must be deterministic.

Regression: _find_shortest_path broke Dijkstra ties with id(node) — the memory
address of the node-id int — so with two equal-cost paths the winner depended on
object allocation, and repeated maintenance runs flip-flopped between
"rerouted" and "no change". Ties now break on values (node id, then path), so
the lexicographically-smallest equal-cost path always wins.
"""
import itertools

import networkx as nx

from app.services.simulation import _find_shortest_path


def _diamond(link_order):
    """A -(1)- B -(1)- D and A -(1)- C -(1)- D: two equal-cost paths (1,2,4)
    and (1,3,4). Edge insertion order varies to shake adjacency ordering."""
    G = nx.MultiGraph()
    edges = {
        "ab": (1, 2, {"id": 12, "weight": 1}),
        "bd": (2, 4, {"id": 24, "weight": 1}),
        "ac": (1, 3, {"id": 13, "weight": 1}),
        "cd": (3, 4, {"id": 34, "weight": 1}),
    }
    for key in link_order:
        u, v, data = edges[key]
        G.add_edge(u, v, **data)
    return G


def test_equal_cost_choice_is_canonical_for_any_insertion_order():
    # Whatever order the links are inserted (i.e. however the DB happened to
    # return rows), the same equal-cost path must win.
    results = set()
    for order in itertools.permutations(["ab", "bd", "ac", "cd"]):
        path, lids = _find_shortest_path(_diamond(order), 1, 4)
        results.add((tuple(path), tuple(lids)))
    assert results == {((1, 2, 4), (12, 24))}  # lexicographically smallest path


def test_removing_the_unchosen_path_is_a_no_op():
    # Maintenance semantics: failing a link on the NOT-chosen equal-cost path
    # must consistently report no change; failing the chosen one must
    # consistently reroute to the other path.
    G = _diamond(["ab", "bd", "ac", "cd"])
    base_path, base_lids = _find_shortest_path(G, 1, 4)
    assert base_path == [1, 2, 4]

    unchosen = _diamond(["ab", "bd", "cd"])         # remove A-C (id 13, unchosen)
    assert _find_shortest_path(unchosen, 1, 4)[0] == base_path

    chosen_failed = _diamond(["ac", "cd", "bd"])    # remove A-B (id 12, chosen)
    assert _find_shortest_path(chosen_failed, 1, 4)[0] == [1, 3, 4]

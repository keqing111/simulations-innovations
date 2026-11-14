"""Symbolic payload validation, independent of scheduler synthesis timing.

This checks logical transfers, not runtime data-dependency timing: open-loop
ns-3 packets do not carry or enforce the symbolic payload dependencies.
"""

from collections import defaultdict, deque

import numpy as np

from .model import Schedule, TrafficMatrix


def validate_fast_delivery(matrix: TrafficMatrix, schedule: Schedule,
                           gpus_per_server: int) -> dict:
    g = gpus_per_server
    holdings = defaultdict(deque)
    delivered = np.zeros_like(matrix.values)
    for src in range(matrix.num_devices):
        for dst in range(matrix.num_devices):
            size = int(matrix.values[src, dst])
            if size:
                holdings[src, dst].append((src, size))

    def consume(owner, dst, amount):
        queue = holdings[owner, dst]
        while amount and queue:
            origin, size = queue.popleft()
            taken = min(size, amount)
            yield origin, taken
            amount -= taken
            if size > taken:
                queue.appendleft((origin, size-taken))
        if amount:
            raise ValueError("FAST plan transfers payload not present at its logical source")

    # Balance is server-local and tagged with its destination-server id.
    for flow in schedule.flows:
        if flow.phase != 'balance':
            continue
        if flow.src//g != flow.dst//g:
            raise ValueError("balance must stay within its source server")
        amount = flow.size_bytes
        for dst in range(flow.stage_id*g, (flow.stage_id+1)*g):
            available = sum(size for _, size in holdings[flow.src, dst])
            take = min(amount, available)
            chunks = list(consume(flow.src, dst, take))
            holdings[flow.dst, dst].extend(chunks)
            amount -= take
        if amount:
            raise ValueError("balance moves more real payload than available")

    for flow in schedule.flows:
        if flow.phase == 'intrinsic':
            if flow.src//g != flow.dst//g:
                raise ValueError("intrinsic flow crosses a server boundary")
            for origin, size in consume(flow.src, flow.dst, flow.size_bytes):
                delivered[origin, flow.dst] += size

    required_dispatch = defaultdict(int)
    for flow in schedule.flows:
        if flow.phase != 'inter':
            continue
        if flow.src//g == flow.dst//g:
            raise ValueError("inter flow must cross a server boundary")
        amount = flow.size_bytes
        base = flow.dst//g*g
        for dst in range(base, base+g):
            available = sum(size for _, size in holdings[flow.src, dst])
            take = min(amount, available)
            for origin, size in consume(flow.src, dst, take):
                delivered[origin, dst] += size
                if flow.dst != dst:
                    required_dispatch[flow.stage_id, flow.dst, dst] += size
            amount -= take
        if amount:
            raise ValueError("inter flow exceeds remaining logical demand")

    actual_dispatch = defaultdict(int)
    for flow in schedule.flows:
        if flow.phase == 'dispatch':
            actual_dispatch[flow.stage_id, flow.src, flow.dst] += flow.size_bytes
    if dict(actual_dispatch) != dict(required_dispatch):
        raise ValueError("FAST destination dispatch does not match received logical payload")
    if any(queue for queue in holdings.values()) or not np.array_equal(delivered, matrix.values):
        raise ValueError("FAST plan did not deliver the original GPU-to-GPU matrix")
    return dict(logical_bytes_delivered=matrix.logical_bytes,
                logical_pairs_delivered=int(np.count_nonzero(delivered)),
                destination_dispatch_verified=True)

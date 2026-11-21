"""Fixed directed resources, whole messages, and stage-free endpoint dependencies.

Units: bytes, Gbit/s, microseconds. EndpointGate is a protocol contract for an
event-driven adapter, NOT a network simulator and provides no timing estimates.
"""
from collections import defaultdict, deque
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Message:
    id: int
    src: int
    dst: int
    bytes: int

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in (self.id,self.src,self.dst)):
            raise ValueError('message IDs/endpoints must be nonnegative integers')
        if self.src == self.dst or type(self.bytes) is not int or self.bytes <= 0:
            raise ValueError('message must have distinct endpoints and positive integer bytes')


@dataclass(frozen=True)
class Link:
    src: int
    dst: int
    gbps: float
    delay_us: float

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in (self.src,self.dst)) or self.src == self.dst:
            raise ValueError('invalid link endpoints')
        if not isfinite(self.gbps) or self.gbps <= 0 or not isfinite(self.delay_us) or self.delay_us < 0:
            raise ValueError('invalid link capacity/delay')


class Network:
    """Input links are full-duplex, stored as independent directed resources."""
    def __init__(self, gpu_count, links, gpus_per_server):
        if type(gpu_count) is not int or type(gpus_per_server) is not int or gpu_count <= 0 or gpus_per_server <= 0 or gpu_count % gpus_per_server:
            raise ValueError('invalid physical GPU grouping')
        self.gpu_count=gpu_count;self.gpus_per_server=gpus_per_server
        self.links={};self.adjacency=defaultdict(list);self._paths={}
        for link in links:
            for s,d in ((link.src,link.dst),(link.dst,link.src)):
                if (s,d) in self.links:raise ValueError('duplicate physical link')
                self.links[s,d]=Link(s,d,link.gbps,link.delay_us);self.adjacency[s].append(d)
        if not set(range(gpu_count)) <= self.adjacency.keys():raise ValueError('missing GPU node')
        reached=set();todo=[0]
        while todo:
            node=todo.pop()
            if node in reached:continue
            reached.add(node);todo.extend(self.adjacency[node])
        if reached != self.adjacency.keys():raise ValueError('disconnected network')

    def path(self, src, dst):
        if any(type(v) is not int or not 0 <= v < self.gpu_count for v in (src,dst)):
            raise ValueError('GPU endpoint outside topology')
        if (src,dst) not in self._paths:
            distance={dst:0};queue=deque([dst])
            while queue:
                node=queue.popleft()
                for neighbor in self.adjacency[node]:
                    if neighbor not in distance:distance[neighbor]=distance[node]+1;queue.append(neighbor)
            node=src;path=[]
            while node != dst:
                choices=sorted(v for v in self.adjacency[node] if distance[v] == distance[node]-1)
                neighbor=choices[(dst//self.gpus_per_server)%len(choices)]
                path.append((node,neighbor));node=neighbor
            self._paths[src,dst]=tuple(path)
        return self._paths[src,dst]

    def delay_us(self,src,dst):
        return sum(self.links[e].delay_us for e in self.path(src,dst))

    def resources(self,messages):
        index=defaultdict(list)
        for f in messages:
            for e in self.path(f.src,f.dst):index[e].append(f.id)
        return {e:tuple(ids) for e,ids in index.items()}

    def work_us(self,messages):
        work=defaultdict(float)
        for f in messages:
            for e in self.path(f.src,f.dst):work[e]+=f.bytes*.008/self.links[e].gbps
        return dict(work)


@dataclass(frozen=True)
class Plan:
    messages: tuple
    order: tuple
    send_after: dict
    receive_after: dict

    @classmethod
    def from_order(cls,messages,order):
        messages=tuple(messages);order=tuple(order);by_id={f.id:f for f in messages}
        if len(by_id)!=len(messages) or len({(f.src,f.dst) for f in messages})!=len(messages):
            raise ValueError('duplicate message ID or GPU pair')
        if any(type(i) is not int for i in order) or len(order)!=len(messages) or set(order)!=set(by_id):
            raise ValueError('order must contain each integer message ID exactly once')
        send={};receive={};last_send={};last_receive={}
        for ident in order:
            f=by_id[ident];send[ident]=last_send.get(f.src);receive[ident]=last_receive.get(f.dst)
            last_send[f.src]=ident;last_receive[f.dst]=ident
        return cls(messages,order,send,receive)

    def dependency_key(self):
        return tuple((i,self.send_after[i],self.receive_after[i]) for i in sorted(self.order))


class EndpointGate:
    """Data admission eligibility; adapter must still exchange receiver credits.

    release() marks actual data start after credit arrival. A receiver reserves
    its slot for the next dependency-ordered message; other messages cannot
    bypass it. Sender slot releases on ACK, receiver slot on full data receipt.
    """
    def __init__(self,plan):
        self.plan=plan;self.started=set();self.data_done=set();self.ack_done=set()

    def ready(self):
        return [i for i in self.plan.order if i not in self.started
                and (self.plan.send_after[i] is None or self.plan.send_after[i] in self.ack_done)
                and (self.plan.receive_after[i] is None or self.plan.receive_after[i] in self.data_done)]

    def release(self,ident):
        if ident not in self.ready():raise ValueError('message not eligible for data release')
        self.started.add(ident)

    def received(self,ident):
        if ident not in self.started or ident in self.data_done:raise ValueError('unexpected data receipt')
        self.data_done.add(ident)

    def acknowledged(self,ident):
        if ident not in self.data_done or ident in self.ack_done:raise ValueError('unexpected ACK')
        self.ack_done.add(ident)

    def finished(self):
        return len(self.ack_done)==len(self.plan.messages)

"""Fixed DC unit, unique destination-based routes and non-rank background hosts."""
from collections import deque
from .topology import Ns3Topology,TopologyLink


def build_leafspine(hw,dcs):
    if hw.num_servers!=8*dcs or hw.gpus_per_server!=4:raise ValueError('v2 requires8 servers/DC and4GPUs/server')
    P=hw.num_devices;bg=tuple(range(P,P+dcs));nv=tuple(range(P+dcs,P+dcs+hw.num_servers))
    start=P+dcs+hw.num_servers;tor=tuple(range(start,start+2*dcs))
    spine=tuple(range(start+2*dcs,start+4*dcs));gw=tuple(range(start+4*dcs,start+5*dcs));links=[]
    def link(a,b,bw,delay):links.append(TopologyLink(a,b,bw,delay))
    for rank in range(P):link(rank,nv[rank//4],8/hw.intra.beta_ns_per_byte,hw.intra.alpha_ns)
    for s in range(hw.num_servers):link(nv[s],tor[s//4],400,700)
    for dc in range(dcs):
        for leaf in tor[dc*2:dc*2+2]:
            for sp in spine[dc*2:dc*2+2]:link(leaf,sp,1600,700)
        for sp in spine[dc*2:dc*2+2]:link(sp,gw[dc],1600,700)
        link(bg[dc],gw[dc],1600,700)
    for a in range(dcs):
        for b in range(a+1,dcs):link(gw[a],gw[b],100,50000)
    return Ns3Topology(tuple(range(P)),nv,tor[0],tuple(links),4,switch_ids=tor+spine+gw,
                       tor_ids=tor,gateway_ids=gw,spine_ids=spine,background_ids=bg)


def fixed_routes(top,*,route_classes=None):
    adjacency={i:[] for i in range(top.node_count)}
    for e in top.links:adjacency[e.src].append(e.dst);adjacency[e.dst].append(e.src)
    routes={}
    # ns-3 also installs direct host-to-NVSwitch destinations internally.
    for dst in range(top.node_count):
        distance={dst:0};queue=deque([dst])
        while queue:
            u=queue.popleft()
            for v in adjacency[u]:
                if v not in distance:distance[v]=distance[u]+1;queue.append(v)
        if len(distance)!=top.node_count:raise ValueError('disconnected fabric')
        for u in adjacency:
            if u==dst:continue
            choices=sorted(v for v in adjacency[u] if distance[v]==distance[u]-1)
            route_class=(route_classes or {}).get(dst,dst//top.gpus_per_server)
            routes[u,dst]=choices[route_class%len(choices)]
    return routes


def server_snapshot(top,hw,routes):
    gpu=set(top.gpu_ids);bg=set(top.background_ids);names=[];rates=[];ids={};edges={}
    for link in top.links:
        for a,b in ((link.src,link.dst),(link.dst,link.src)):
            edges[a,b]=link
            if a in gpu|bg or b in gpu|bg:continue
            ids[a,b]=len(names)
            names.append(f'wan:{top.gateway_ids.index(a)}->{top.gateway_ids.index(b)}'
                         if a in top.gateway_ids and b in top.gateway_ids else f'link:{a}->{b}')
            rates.append(link.bandwidth_gbps)
    paths=[]
    for s in range(hw.num_servers):
        for d in range(hw.num_servers):
            if s==d:paths.append(dict(resource_ids=[],propagation_us=0.));continue
            u=top.nvswitch_ids[s];target=top.nvswitch_ids[d];path=[];tau=2*hw.intra.alpha_ns
            while u!=target:
                v=routes[u,d*hw.gpus_per_server];path.append(ids[u,v]);tau+=edges[u,v].latency_ns;u=v
                if len(path)>top.node_count:raise ValueError('route loop')
            paths.append(dict(resource_ids=path,propagation_us=tau/1000))
    return dict(n=hw.num_servers,g=hw.gpus_per_server,resource_names=names,bandwidth_gbps=rates,server_paths=paths)


def write_fabric(top,routes,root):
    (root/'topology.txt').write_text(top.serialize())
    (root/'routes.csv').write_text(''.join(f'{u},{dst},{v}\n' for (u,dst),v in sorted(routes.items())))
    next_port={};wan=[];all_ports=[]
    for e in top.links:
        a,b=e.src,e.dst;pa=next_port.get(a,1);pb=next_port.get(b,1);next_port[a]=pa+1;next_port[b]=pb+1
        if a in top.regular_switch_ids:all_ports.append([a,pa])
        if b in top.regular_switch_ids:all_ports.append([b,pb])
        if a in top.gateway_ids and b in top.gateway_ids:wan.extend([[a,pa],[b,pb]])
    return dict(kind='v2-leaf-spine',gpu_ids=list(top.gpu_ids),nvswitch_ids=list(top.nvswitch_ids),
        tor_id=top.tor_id,tor_ids=list(top.tor_ids),spine_ids=list(top.spine_ids),gateway_ids=list(top.gateway_ids),
        background_ids=list(top.background_ids),foreground_gpu_count=len(top.gpu_ids),
        gateway_wan_ports=wan,all_switch_ports=all_ports,tor_port_capacity_gbps=1600,
        gateway_wan_port_capacity_gbps=100)

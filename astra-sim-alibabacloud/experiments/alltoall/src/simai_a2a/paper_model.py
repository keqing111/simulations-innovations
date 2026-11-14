"""Explicit multi-DC extension of FAST's alpha/beta sequential epoch model.

Epoch alpha is a user-specified effective latency, NOT implicitly an RTT.
Serialization is a shared-resource fluid estimate, not packet-exact FCT.
"""

from dataclasses import asdict,dataclass
from numbers import Integral,Real
import math
import numpy as np


@dataclass(frozen=True)
class PaperTopology:
    dc_ids: tuple[int,...]
    server_gbps: float
    wan_gbps: float
    lan_alpha_ns: float
    wan_alpha_ns: float
    gateway_gbps: float|None=None

    def __post_init__(self):
        if not isinstance(self.dc_ids,(list,tuple)) or not self.dc_ids or any(
                isinstance(x,bool) or not isinstance(x,Integral) or x<0 for x in self.dc_ids):
            raise ValueError('dc_ids must be nonempty nonnegative integer labels')
        object.__setattr__(self,'dc_ids',tuple(map(int,self.dc_ids)))
        if self.gateway_gbps is None:object.__setattr__(self,'gateway_gbps',self.server_gbps)
        for name in ('server_gbps','wan_gbps','gateway_gbps','lan_alpha_ns','wan_alpha_ns'):
            x=getattr(self,name)
            if isinstance(x,bool) or not isinstance(x,Real) or not math.isfinite(x) or (
                    x<=0 if name.endswith('gbps') else x<0):
                raise ValueError(f'invalid {name}')

    def to_dict(self):
        return {**asdict(self),'dc_ids':list(self.dc_ids)}

    def stage(self,demand):
        n=len(self.dc_ids);a=np.asarray(demand)
        if (a.shape!=(n,n) or not np.issubdtype(a.dtype,np.integer) or np.any(a<0)
                or np.any(np.diag(a))):
            raise ValueError('real server demand must be a nonnegative integer square matrix with zero diagonal')
        dc=sorted(set(self.dc_ids));loads={};rates={}
        def add(key,amount,rate):
            loads[key]=loads.get(key,0)+int(amount);rates[key]=float(rate)
        for s in range(n):
            add(f'server_out:{s}',0,self.server_gbps);add(f'server_in:{s}',0,self.server_gbps)
        for x in dc:
            add(f'gateway_out:{x}',0,self.gateway_gbps);add(f'gateway_in:{x}',0,self.gateway_gbps)
            for y in dc:
                if x!=y:add(f'wan:{x}:{y}',0,self.wan_gbps)
        alpha=0.;total=wan=0
        for s,d in zip(*np.nonzero(a)):
            size=int(a[s,d]);total+=size
            add(f'server_out:{s}',size,self.server_gbps);add(f'server_in:{d}',size,self.server_gbps)
            x,y=self.dc_ids[s],self.dc_ids[d]
            cross=x!=y
            alpha=max(alpha,self.wan_alpha_ns if cross else self.lan_alpha_ns)
            if cross:
                wan+=size
                add(f'wan:{x}:{y}',size,self.wan_gbps)
                add(f'gateway_out:{x}',size,self.gateway_gbps)
                add(f'gateway_in:{y}',size,self.gateway_gbps)
        busy={key:8*size/rates[key] for key,size in loads.items()}
        serial=max(busy.values(),default=0.)
        return dict(duration_ns=alpha+serial,alpha_ns=alpha,serialization_ns=serial,
                    real_bytes=total,wan_bytes=wan,resource_bytes=loads,resource_busy_ns=busy,
                    bottlenecks=[k for k,v in busy.items() if serial and v==serial])

    def lower_bound(self,demand):
        """Serialization cut bound only: excludes alpha, scale-up and ordering."""
        return self.stage(demand)['serialization_ns']

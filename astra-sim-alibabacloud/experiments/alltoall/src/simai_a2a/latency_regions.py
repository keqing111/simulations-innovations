"""Exact affine extension of fixed epoch plans on integer WAN-alpha nanoseconds.

The candidate decompositions must not depend on alpha. Results are model
boundaries, NOT measured RTT or packet-level thresholds. ceil(alpha+tau) equals
alpha+ceil(tau) for integer alpha; the latter defines the continuous extension.
"""

import math


def _curve(schedule):
    meta=schedule.metadata
    if not meta['paper_stages']:
        fixed=float(meta['analytical_makespan_ns'])
        if not math.isfinite(fixed) or fixed<0:raise ValueError('invalid local-only cost')
        return fixed,[]
    base=float(meta['balance_ns'])+float(meta['final_redistribution_ns']);terms=[]
    for stage in meta['paper_stages']:
        prior=float(stage['scaleup_overlap_ns'])
        tau=float(stage['serialization_ns'])
        if not math.isfinite(prior+tau) or min(prior,tau)<0:raise ValueError('invalid epoch evidence')
        if stage['wan_bytes']:
            terms.append((math.ceil(tau),prior))
        else:base+=max(float(stage['duration_ns']),prior)
    if not math.isfinite(base) or base<0:raise ValueError('invalid fixed pipeline costs')
    return base,terms


def curve_time(schedule,alpha_ns):
    if not math.isfinite(alpha_ns) or alpha_ns<schedule.metadata['paper_model']['lan_alpha_ns']:
        raise ValueError('WAN-alpha domain must be finite and at least LAN alpha')
    base,terms=_curve(schedule)
    return base+sum(max(alpha_ns+tau,prior) for tau,prior in terms)


def phase_regions(schedules,alpha_min,alpha_max):
    if not schedules or not math.isfinite(alpha_min) or not math.isfinite(alpha_max) or alpha_min>=alpha_max:
        raise ValueError('empty candidates or invalid latency interval')
    if alpha_min<max(s.metadata['paper_model']['lan_alpha_ns'] for s in schedules):
        raise ValueError('WAN alpha must not be below LAN alpha')
    names=[s.algorithm for s in schedules]
    if len(set(names))!=len(names):raise ValueError('candidate names must be unique')
    curves=[_curve(s) for s in schedules];hinges={float(alpha_min),float(alpha_max)}
    for base,terms in curves:
        for tau,prior in terms:
            if alpha_min<prior-tau<alpha_max:hinges.add(prior-tau)
    def affine(curve,point):
        base,terms=curve;slope=0;intercept=base
        for tau,prior in terms:
            if point+tau>prior:slope+=1;intercept+=tau
            else:intercept+=prior
        return slope,intercept
    cuts=set(hinges);ordered=sorted(hinges)
    for lower,upper in zip(ordered,ordered[1:]):
        lines=[affine(curve,(lower+upper)/2) for curve in curves]
        for i,(a,b) in enumerate(lines):
            for c,d in lines[i+1:]:
                if a!=c:
                    root=(d-b)/(a-c)
                    if lower<root<upper:cuts.add(root)
    def winner(point):
        costs=[base+sum(max(point+tau,prior) for tau,prior in terms) for base,terms in curves]
        best=0
        for i in range(1,len(costs)):
            if costs[i]<costs[best]-1e-6:best=i
        return best
    points=sorted(cuts);regions=[]
    for lower,upper in zip(points,points[1:]):
        midpoint=(lower+upper)/2;best=winner(midpoint);slope,intercept=affine(curves[best],midpoint)
        regions.append(dict(lower_alpha_ns=lower,upper_alpha_ns=upper,winner=names[best],
                            slope=slope,intercept_ns=intercept))
    return dict(regions=regions,boundaries=[dict(alpha_ns=p,winner=names[winner(p)]) for p in points],
                model='fixed bandwidth and fixed decompositions; affine extension exact at integer alpha ns')

#include "planner.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>

namespace native_scan {
Options Options::parse(const std::string& method){
    Options o;
    if(method=="A1"||method=="A2"){o.exact=true;o.constructive_budget=method=="A1"?1:2;return o;}
    if(method=="maxmin"){o.exact=true;return o;}
    if(method=="X10"||method=="X01"||method=="X11"||method=="X11-4"||
       method=="Q75"||method=="Q50"||method=="B11"||method=="B11-4"){
        o.exact=true;o.link=method!="X01";o.residual=method!="X10";
        if(method=="Q75"){o.quota_numerator=3;o.quota_denominator=4;}
        if(method=="Q50"){o.quota_numerator=1;o.quota_denominator=2;}
        o.tail_score=method=="B11"||method=="B11-4";
        if(method=="X11-4"||method=="B11-4")o.cycle_budget=4;
        return o;
    }
    if(method.size()!=4 || method[0]!='F' || method.find_first_not_of("01",1)!=std::string::npos)
        throw std::invalid_argument("unknown G/L/R method");
    o.granularity=method[1]=='1';o.link=method[2]=='1';o.residual=method[3]=='1';return o;
}

ResourceCache::ResourceCache(const native_topology::Topology& t):n(t.n),g(t.g),dc(t.dc),routes(n*n),alpha(n*n){
    std::map<std::string,unsigned> ids;
    for(const auto& item:t.bandwidths){ids[item.first]=bandwidth.size();bandwidth.push_back(item.second);}
    loads.resize(bandwidth.size());
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d){alpha[s*n+d]=t.path_alpha(s,d);
        for(const auto& key:t.path(s,d))routes[s*n+d].push_back(ids.at(key));}
}
CostSummary ResourceCache::score(const std::vector<int>& m,unsigned w,const std::vector<uint64_t>& rows){
    if(m.size()!=n || rows.size()!=n*n*g)throw std::invalid_argument("resource dimensions");
    std::fill(loads.begin(),loads.end(),0);CostSummary result;double delay=0,serial=0;
    for(unsigned s=0;s<n;++s){unsigned d=m[s];if(d>=n)throw std::invalid_argument("destination range");if(s==d)continue;
        uint64_t volume=0;for(unsigned u=0;u<g;++u)volume+=std::min<uint64_t>(w,rows[(s*n+d)*g+u]);
        if(!volume)continue;result.bytes+=volume;delay=std::max(delay,alpha[s*n+d]);
        for(unsigned e:routes[s*n+d])loads[e]+=volume;
    }
    for(unsigned e=0;e<loads.size();++e)serial=std::max(serial,.008*double(loads[e])/bandwidth[e]);
    result.us=result.bytes?delay+serial:0;return result;
}
std::vector<double> ResourceCache::priorities(const std::vector<uint64_t>& rows){
    if(rows.size()!=n*n*g)throw std::invalid_argument("residual dimensions");
    std::fill(loads.begin(),loads.end(),0);std::vector<uint64_t> volumes(n*n);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d){
        auto& v=volumes[s*n+d];for(unsigned u=0;u<g;++u)v+=rows[(s*n+d)*g+u];
        for(unsigned e:routes[s*n+d])loads[e]+=v;
    }
    double maximum=0;std::vector<double> pressure(loads.size());
    for(unsigned e=0;e<loads.size();++e){pressure[e]=double(loads[e])/bandwidth[e];maximum=std::max(maximum,pressure[e]);}
    std::vector<double> priorities(n*n);
    if(!maximum)return priorities;
    for(unsigned k=0;k<n*n;++k)if(volumes[k])for(unsigned e:routes[k])
        priorities[k]+=double(volumes[k])*(pressure[e]/maximum)/bandwidth[e];
    return priorities;
}

bool ResourceCache::balanced_matching(const std::vector<unsigned>& a,const std::vector<std::vector<unsigned>>& neighbors,
                                     unsigned q,const std::vector<uint64_t>& rows,const std::vector<unsigned>& order,std::vector<int>& m){
    if(a.size()!=n*n||neighbors.size()!=n||rows.size()!=n*n*g||order.size()!=n||!q)
        throw std::invalid_argument("constructive dimensions");
    std::vector<uint64_t> volume(n*n),used(bandwidth.size());
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d)
        for(unsigned u=0;u<g;++u)volume[s*n+d]+=std::min<uint64_t>(q,rows[(s*n+d)*g+u]);
    m.assign(n,-1);std::vector<int> inverse(n,-1);std::vector<bool> seen(n);
    std::function<bool(unsigned)> dfs=[&](unsigned s){
        // Ranking is read-only. Remove this row's current assignment in a local
        // view; commit resource updates only on a successful augmenting path.
        auto reduced=used;
        if(m[s]>=0)for(unsigned e:routes[s*n+m[s]])reduced[e]-=volume[s*n+m[s]];
        double base=0;for(unsigned e=0;e<reduced.size();++e)base=std::max(base,double(reduced[e])/bandwidth[e]);
        struct Candidate{unsigned d;double peak,square;};std::vector<Candidate> candidates;
        for(unsigned d:neighbors[s])if(a[s*n+d]>=q&&!seen[d]){
            double peak=base,square=0;auto v=volume[s*n+d];
            for(unsigned e:routes[s*n+d]){double old=double(reduced[e])/bandwidth[e],next=double(reduced[e]+v)/bandwidth[e];
                peak=std::max(peak,next);square+=(next-old)*(next+old);}
            candidates.push_back({d,peak,square});
        }
        std::stable_sort(candidates.begin(),candidates.end(),[](const Candidate& x,const Candidate& y){
            return x.peak!=y.peak?x.peak<y.peak:x.square<y.square;});
        for(const auto& candidate:candidates){unsigned d=candidate.d;if(seen[d])continue;seen[d]=true;
            if(inverse[d]<0||dfs(inverse[d])){
                if(m[s]>=0)for(unsigned e:routes[s*n+m[s]])used[e]-=volume[s*n+m[s]];
                for(unsigned e:routes[s*n+d])used[e]+=volume[s*n+d];m[s]=d;inverse[d]=s;return true;}
        }
        return false;
    };
    std::vector<bool> ordered(n);
    for(unsigned s:order){if(s>=n||ordered[s])throw std::invalid_argument("constructive row order");ordered[s]=true;
        std::fill(seen.begin(),seen.end(),false);if(!dfs(s))return false;}
    return true;
}

double ResourceCache::remaining_bound(const std::vector<int>& m,unsigned w,const std::vector<uint64_t>& rows){
    if(m.size()!=n||rows.size()!=n*n*g)throw std::invalid_argument("remaining bound dimensions");
    std::fill(loads.begin(),loads.end(),0);
    std::vector<unsigned> out(n),in(n),wan_out(n),wan_in(n);
    double min_all=std::numeric_limits<double>::infinity(),min_wan=min_all,serial=0;
    for(unsigned s=0;s<n;++s){if(m[s]<0||m[s]>=int(n))throw std::invalid_argument("bound destination range");
        for(unsigned d=0;d<n;++d)if(s!=d){uint64_t bytes=0;
            for(unsigned u=0;u<g;++u){auto v=rows[(s*n+d)*g+u];bytes+=v-(m[s]==int(d)?std::min<uint64_t>(v,w):0);}
            if(!bytes)continue;++out[s];++in[d];min_all=std::min(min_all,alpha[s*n+d]);
            if(dc[s]!=dc[d]){++wan_out[s];++wan_in[d];min_wan=std::min(min_wan,alpha[s*n+d]);}
            for(unsigned e:routes[s*n+d])loads[e]+=bytes;
        }
    }
    for(unsigned e=0;e<loads.size();++e)serial=std::max(serial,.008*double(loads[e])/bandwidth[e]);
    unsigned degree=0,wan_degree=0;
    for(unsigned s=0;s<n;++s){degree=std::max(degree,std::max(out[s],in[s]));wan_degree=std::max(wan_degree,std::max(wan_out[s],wan_in[s]));}
    return serial+std::max(degree?degree*min_all:0,wan_degree?wan_degree*min_wan:0);
}

Planner::Planner(const native_topology::Topology& t,std::vector<uint64_t> rows,const std::string& method)
    :top(t),remaining(std::move(rows)),options(Options::parse(method)){
    if(remaining.size()!=top.n*top.n*top.g)throw std::invalid_argument("planner dimensions");
    auto start=std::chrono::steady_clock::now();
    if(options.link||options.residual||options.constructive_budget)resources.reset(new ResourceCache(top));
    preprocess_us=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count();
}
static unsigned coefficient(const std::vector<unsigned>& a,unsigned n,const std::vector<int>& m){
    unsigned w=UINT32_MAX;std::vector<bool> seen(n);
    if(m.size()!=n)throw std::invalid_argument("matching dimension");
    for(unsigned s=0;s<n;++s){int d=m[s];
        if(d<0||d>=int(n)||seen[d]||!a[s*n+d])throw std::invalid_argument("matching not supported perfect");
        seen[d]=true;w=std::min(w,a[s*n+d]);}
    return w;
}
static bool find_matching(const std::vector<unsigned>& a,const std::vector<std::vector<unsigned>>& neighbors,
                          unsigned q,std::vector<int>& m){
    unsigned n=neighbors.size();m.assign(n,-1);std::vector<int> inverse(n,-1);std::vector<bool> seen(n);
    std::function<bool(unsigned)> dfs=[&](unsigned s){
        for(unsigned d:neighbors[s])if(a[s*n+d]>=q && !seen[d]){
            seen[d]=true;if(inverse[d]<0 || dfs(inverse[d])){m[s]=d;inverse[d]=s;return true;}}
        return false;
    };
    for(unsigned s=0;s<n;++s){std::fill(seen.begin(),seen.end(),false);if(!dfs(s))return false;}
    return true;
}

Decision Planner::choose(const std::vector<unsigned>& a,const hungarian_info_t& info){
    unsigned n=top.n,g=top.g;if(a.size()!=n*n||info.matching.size()!=2*n||info.row_to_col.size()!=n)
        throw std::invalid_argument("matching metadata dimensions");
    Decision result;result.matching.resize(n);std::vector<std::vector<unsigned>> neighbors(n);
    for(unsigned s=0;s<n;++s){result.matching[s]=info.matching[s]-n;
        for(unsigned d:info.row_to_col[s]){if(d<n||d>=2*n)throw std::invalid_argument("neighbor range");neighbors[s].push_back(d-n);}}
    auto original=result.matching;unsigned original_weight=coefficient(a,n,original);
    result.native_weight=original_weight;
    std::vector<double> priorities;
    if(options.residual){priorities=resources->priorities(remaining);
        for(unsigned s=0;s<n;++s)std::stable_sort(neighbors[s].begin(),neighbors[s].end(),[&](unsigned x,unsigned y){return priorities[s*n+x]>priorities[s*n+y];});}
    auto feasible=[&](unsigned q,std::vector<int>& trial){++counts.matching_calls;return find_matching(a,neighbors,q,trial);};
    if(options.exact){
        std::vector<unsigned> values=a;values.erase(std::remove(values.begin(),values.end(),0),values.end());
        std::sort(values.begin(),values.end());values.erase(std::unique(values.begin(),values.end()),values.end());
        int lo=0,hi=int(values.size())-1;
        while(lo<=hi){int mid=(lo+hi)/2;std::vector<int> trial;
            if(feasible(values[mid],trial)){result.matching=std::move(trial);result.threshold=values[mid];lo=mid+1;}else hi=mid-1;}
        result.exact_weight=result.threshold;
        if(options.quota_numerator<options.quota_denominator){
            result.threshold=std::max(original_weight,unsigned((uint64_t(result.exact_weight)*options.quota_numerator+options.quota_denominator-1)/options.quota_denominator));
            if(!feasible(result.threshold,result.matching))throw std::runtime_error("relaxed exact threshold lost feasibility");
        }
    }else if(options.granularity){
        unsigned maximum=*std::max_element(a.begin(),a.end());std::vector<unsigned> powers;
        for(uint64_t q=1;q<=maximum;q*=2)powers.push_back(unsigned(q));
        int lo=0,hi=int(powers.size())-1;unsigned q2=1;
        while(lo<=hi){int mid=(lo+hi)/2;std::vector<int> trial;++result.probes;++counts.threshold_probes;
            if(feasible(powers[mid],trial)){q2=powers[mid];lo=mid+1;}else hi=mid-1;}
        result.threshold=std::max(q2,original_weight);
        if(!feasible(result.threshold,result.matching))throw std::runtime_error("known feasible coarse threshold lost");
        if(result.probes>6)throw std::runtime_error("G probe budget");
    }else if(options.residual){
        if(!feasible(1,result.matching))throw std::runtime_error("positive residual lost perfect matching");
    }
    result.seed_weight=coefficient(a,n,result.matching);
    if(options.constructive_budget){
        auto score=[&](const std::vector<int>& m){auto value=resources->score(m,coefficient(a,n,m),remaining);
            return value.bytes?value.us/double(value.bytes):std::numeric_limits<double>::infinity();};
        double best=score(result.matching);std::vector<unsigned> order(n);std::iota(order.begin(),order.end(),0);
        for(unsigned k=0;k<options.constructive_budget;++k){
            if(k){std::map<int,std::vector<unsigned>> groups;for(unsigned s=0;s<n;++s)groups[top.dc[s]].push_back(s);
                order.clear();for(unsigned pos=0;order.size()<n;++pos)for(const auto& item:groups)if(pos<item.second.size())order.push_back(item.second[pos]);}
            std::vector<int> trial;++counts.construction_trials;++result.constructions;++counts.matching_calls;
            if(!resources->balanced_matching(a,neighbors,result.threshold,remaining,order,trial))
                throw std::runtime_error("constructive matching lost feasible threshold");
            if(coefficient(a,n,trial)<result.seed_weight)throw std::runtime_error("constructive coefficient regression");
            double value=score(trial);if(value<best-1e-18){best=value;result.matching=std::move(trial);++counts.construction_accepted;}
        }
    }
    if(options.link){
        std::vector<unsigned> mask=a;for(auto& v:mask)if(v<result.threshold)v=0;
        struct Edge {unsigned s,d;uint64_t bytes;double priority;};std::vector<Edge> edges;
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(mask[s*n+d]&&s!=d&&result.matching[s]!=int(d)){
            uint64_t real=0;for(unsigned u=0;u<g;++u)real+=remaining[(s*n+d)*g+u];
            if(real)edges.push_back({s,d,real,options.residual?priorities[s*n+d]:double(real)});
        }
        auto before=[](const Edge& x,const Edge& y){return x.priority!=y.priority?x.priority>y.priority:
            x.s!=y.s?x.s<y.s:x.d<y.d;};
        unsigned budget=std::min<size_t>(options.cycle_budget,edges.size());std::partial_sort(edges.begin(),edges.begin()+budget,edges.end(),before);
        auto score=[&](const std::vector<int>& m){unsigned w=coefficient(a,n,m);auto value=resources->score(m,w,remaining);
            if(!value.bytes)return std::numeric_limits<double>::infinity();
            return options.tail_score?value.us+resources->remaining_bound(m,w,remaining):value.us/double(value.bytes);};
        auto seed=result.matching;double best=score(seed);
        for(unsigned k=0;k<budget;++k){auto trial=seed;++counts.cycle_trials;
            if(!native_topology::force_edge(mask,n,trial,edges[k].s,edges[k].d)){++counts.infeasible_cycles;continue;}
            if(coefficient(a,n,trial)<result.seed_weight){++counts.lower_coefficient_rejections;continue;}
            double value=score(trial);if(value<best-1e-18){best=value;result.matching=std::move(trial);}
            else ++counts.nonimproving_rejections;
        }
    }
    result.weight=coefficient(a,n,result.matching);
    if(result.matching!=original)++counts.changed_matches;
    return result;
}
void Planner::consume(const std::vector<int>& m,unsigned w){
    for(unsigned s=0;s<top.n;++s)for(unsigned u=0;u<top.g;++u){auto& v=remaining[(s*top.n+m[s])*top.g+u];v-=std::min<uint64_t>(v,w);}
}
uint64_t Planner::remaining_inter_bytes()const{
    uint64_t sum=0;for(unsigned s=0;s<top.n;++s)for(unsigned d=0;d<top.n;++d)if(s!=d)
        for(unsigned u=0;u<top.g;++u)sum+=remaining[(s*top.n+d)*top.g+u];return sum;
}
void Planner::decompose_into(FastAll2All& app){
    if(counts.decomposition_runs)throw std::logic_error("planner is single-use");++counts.decomposition_runs;
    app.to_scaled_doubly_stochastic_matrix();
    if(!options.granularity&&!options.link&&!options.residual&&!options.exact){
        app.decompose();for(const auto& p:app.p_sets){std::vector<int> m(top.n);for(unsigned s=0;s<top.n;++s)m[s]=p.mp.at(s);consume(m,p.get_freq());}
        counts.stages=app.p_sets.size();
    }else app.decompose_with([&](Matrix& residual,hungarian_info_t& info){
        if(++counts.stages>top.n*top.n)throw std::runtime_error("finite support bound");
        std::vector<unsigned> a(top.n*top.n);for(unsigned s=0;s<top.n;++s)for(unsigned d=0;d<top.n;++d)a[s*top.n+d]=residual.get(s,d);
        auto decision=choose(a,info);for(unsigned s=0;s<top.n;++s){int d=decision.matching[s];info.matching[s]=top.n+d;info.matching[top.n+d]=s;}
        consume(decision.matching,decision.weight);counts.decisions.push_back(std::move(decision));
    });
    if(remaining_inter_bytes())throw std::runtime_error("decomposition left true demand");
}
}

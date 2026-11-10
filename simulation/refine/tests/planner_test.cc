// refine 构建测试：精确 Max-Min、配额松弛与剩余下界 B11。
//   1. 解析：X*/Q*/B* 必须打开 exact，-4 后缀只改变 cycle_budget；
//   2. remaining_bound 的手算值（跨 DC、部分已扣减、本地链路）；
//   3. Q75/Q50 的 threshold 是 q* 的向上取整比例，且最终系数不低于它；
//   4. 随机矩阵上所有方法守恒，并用“每阶段资源时间之和 ≥ 初始下界”检查
//      下界确实是乐观下界（模型时间不能低于它）。
#include "planner.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <numeric>
#include <random>
using namespace native_scan;
using native_topology::Topology;
// 构造一个指定完美匹配的 hungarian_info；支持图取 a>0 的边。
static hungarian_info_t metadata(const std::vector<unsigned>& a,unsigned n,std::vector<int> m){
    hungarian_info_t info;info.matching.assign(2*n,-1);info.row_to_col.resize(n);
    for(unsigned s=0;s<n;++s){info.matching[s]=n+m[s];info.matching[n+m[s]]=s;
        for(unsigned d=0;d<n;++d)if(a[s*n+d])info.row_to_col[s].insert(n+d);}
    return info;
}
int main(){
    // 方法名解析：X/Q/B 都是精确搜索家族，-4 允许每阶段 4 次 L 修补。
    for(const std::string& name:{"X10","X01","X11","X11-4","Q75","Q50","B11","B11-4"}){
        auto o=Options::parse(name);assert(o.exact);assert(o.cycle_budget==(name.find("-4")!=std::string::npos?4:2));
    }
    // 2 server 跨 DC：真实剩余 0->1 为 1e8、1->0 为 2e8。
    //   {0,1}, w=1            : 两条流都按 1 走 -> 共享 WAN 的串行 16052.8
    //   {1,0}, w=1e8          : 只剩反向 1e8 -> 8052.8
    //   {1,0}, w=2e8          : 反向全部搬完 -> 0
    //   空剩余                : 0
    Topology top(2,1,{0,1},400,400,100,50);ResourceCache resource(top);
    std::vector<uint64_t> rows={0,100000000,200000000,0};
    assert(std::abs(resource.remaining_bound({0,1},1,rows)-16052.8)<1e-8);
    assert(std::abs(resource.remaining_bound({1,0},100000000,rows)-8052.8)<1e-8);
    assert(resource.remaining_bound({1,0},200000000,rows)==0);
    assert(resource.remaining_bound({0,1},1,std::vector<uint64_t>(4))==0);
    // 同 DC 链路只有 server 资源，两个方向互不共享带宽：
    // 较大的反向流 2e8B / 400Gbps = 4000us，加固定 1.4us，故为 4001.4。
    Topology local(2,1,{0,0});ResourceCache lan(local);
    assert(std::abs(lan.remaining_bound({0,1},1,rows)-4001.4)<1e-8);
    // 原生匹配系数只有 1，精确搜索能提升到 100（置换 0<->1）。
    std::vector<unsigned> a={1,100,100,1};auto info=metadata(a,2,{0,1});
    for(const std::string& method:{"X11","Q75","Q50"}){
        Planner p(top,{0,100,100,0},method);auto d=p.choose(a,info);
        assert(d.exact_weight==100 && d.native_weight==1);
        assert(d.threshold==(method=="X11"?100:method=="Q75"?75:50));
        assert(d.weight>=d.threshold && d.weight>=d.seed_weight);
    }
    // 随机矩阵：所有 X/Q/B/maxmin 方法。
    std::mt19937 rng(291030);
    for(unsigned n:{3U,6U,12U})for(unsigned trial=0;trial<4;++trial){
        unsigned g=2;std::vector<int> dc(n);for(unsigned s=0;s<n;++s)dc[s]=s*3/n;
        Topology t(n,g,dc);std::vector<unsigned> service(n*n);std::vector<uint64_t> real(n*n*g);
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d){service[s*n+d]=1+rng()%10000;
            for(unsigned u=0;u<g;++u)real[(s*n+d)*g+u]=service[s*n+d]-u;}
        // identity 置换 + 系数 1 给出初始剩余下界；任何可行计划的资源时间和
        // 都不能低于这个乐观估计。
        ResourceCache bounds(t);std::vector<int> identity(n);std::iota(identity.begin(),identity.end(),0);
        double lb=bounds.remaining_bound(identity,1,real);
        for(const std::string& method:{"maxmin","X10","X01","X11","X11-4","Q75","Q50","B11","B11-4"}){
            Matrix m(service.data(),n);FastAll2All app(&m,g,ETHER400,H100,1e-6);Planner p(t,real,method);p.decompose_into(app);
            assert(app.verify_decomposition());assert(p.remaining_inter_bytes()==0);assert(app.p_sets.size()<=n*n);
            auto options=Options::parse(method);assert(p.stats().cycle_trials<=options.cycle_budget*app.p_sets.size());
            // 按阶段重放真实剩余，逐阶段调用拓扑模型并累加。
            auto left=real;double sum=0;
            for(const auto& stage:app.p_sets){std::vector<native_topology::Transfer> flows;
                for(unsigned s=0;s<n;++s){unsigned d=stage.mp.at(s);uint64_t bytes=0;
                    for(unsigned u=0;u<g;++u){auto& v=left[(s*n+d)*g+u];auto sent=std::min<uint64_t>(v,stage.get_freq());v-=sent;bytes+=sent;}
                    flows.push_back({s,d,bytes});}
                sum+=t.evaluate(flows).total_us;}
            assert(lb<=sum+1e-8);
            // threshold 公式：max(native, ceil(exact * quota_num / quota_den))。
            for(auto d:p.stats().decisions){assert(d.weight>=d.seed_weight && d.weight>=d.threshold);
                assert(d.exact_weight>=d.native_weight);
                assert(d.threshold==std::max(d.native_weight,unsigned((uint64_t(d.exact_weight)*options.quota_numerator+options.quota_denominator-1)/options.quota_denominator)));}
        }
    }
    std::cout<<"Exact/slack/bound strategies: literal bounds, threshold floors, all-method conservation and modeled lower-bound checks passed\n";
}

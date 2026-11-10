// scan Planner 的单元/性质测试。覆盖四类断言：
//   1. 退化策略必须与核心/参考实现逐阶段一致（F000 原生、F010 bounded、maxmin）；
//   2. 真实需求守恒：逐 GPU 剩余最终归零，虚拟补齐不被当作真实流量；
//   3. 系数守卫：不允许用“更低系数”的换匹配通过 L 修补；
//   4. 资源模型手算值、压力优先级、uint32 上界和随机矩阵上的通用不变量。
#include "planner.h"
#include "cached.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <random>
#include <set>
using namespace native_scan;
using native_topology::Topology;

// 把 (矩阵, 指定完美匹配 m) 包装成 Planner::choose 需要的 hungarian_info_t：
// matching 同时记录行->列和列->行，row_to_col 只保留矩阵 >0 的边。
static hungarian_info_t info_for(const std::vector<unsigned>& a,unsigned n,const std::vector<int>& m){
    hungarian_info_t info;info.matching.assign(2*n,-1);info.row_to_col.resize(n);
    for(unsigned s=0;s<n;++s){info.matching[s]=n+m[s];info.matching[n+m[s]]=s;
        for(unsigned d=0;d<n;++d)if(a[s*n+d])info.row_to_col[s].insert(n+d);}
    return info;
}
// 两个分解结果逐阶段比较：置换相同且（换算回原始单位的）频次相同。
static void equal(const std::vector<PermutationSet>& a,const std::vector<PermutationSet>& b){
    assert(a.size()==b.size());for(unsigned k=0;k<a.size();++k){assert(a[k].mp==b[k].mp);assert(a[k].get_freq()==b[k].get_freq());}
}
int main(){
    // 非法方法名必须被拒绝（F 后只能跟三个 0/1 开关）。
    bool invalid=false;try{Options::parse("F200");}catch(const std::invalid_argument&){invalid=true;}assert(invalid);
    // A low coefficient alternating cycle must not pass L's guard.
    // 矩阵的原始匹配是 (0->1,1->2,2->0)，系数 10；交替环能把它换成
    // (0->2,1->0,2->1)，但新系数只有 1。L 修复必须拒绝这种“变差”的环。
    std::vector<unsigned> a={0,10,1,1,0,10,10,1,0};
    std::vector<uint64_t> rows(a.begin(),a.end());
    Topology top3(3,1,{0,1,2},400,400,100,50);
    Planner guarded(top3,rows,"F010");auto info=info_for(a,3,{1,2,0});
    auto choice=guarded.choose(a,info);
    assert(choice.matching==std::vector<int>({1,2,0}));assert(choice.weight==10);
    assert(guarded.stats().lower_coefficient_rejections>0);

    // Shared-WAN cost and current real-only residual priority, hand computed.
    // 6 个 server、每 DC 2 个。两条 1e8 单位的真实流分别走 DC0->DC1 和
    // DC1->DC0，共享同一条 100 Gbps WAN；手算耗时 16052.8us（含 50us WAN 延迟）。
    // priorities 只应标记确实承载真实需求的 server 对（索引 2 与 9），
    // 其余边即使存在于支持图也应为 0。
    Topology top6(6,1,{0,0,1,1,2,2},400,400,100,50);
    ResourceCache resources(top6);std::vector<uint64_t> real(36);
    real[0*6+2]=100000000;real[1*6+3]=100000000;
    auto cost=resources.score({2,3,0,1,5,4},100000000,real);
    assert(cost.bytes==200000000);assert(std::abs(cost.us-16052.8)<1e-8);
    auto priority=resources.priorities(real);
    assert(priority[2]>0 && priority[9]>0 && priority[4]==0 && priority[0]==0);
    auto empty=resources.priorities(std::vector<uint64_t>(36));for(double v:empty)assert(v==0);

    // G has a feasible coarse bound even at the uint32 upper end.
    // 粗边界探测用整数二分，不能因为 (lo+hi) 溢出而漏掉最大可行 q。
    std::vector<unsigned> big={UINT32_MAX,0,0,UINT32_MAX};
    Topology top2(2,1,{0,1});Planner large(top2,std::vector<uint64_t>(4),"F100");
    auto hi=large.choose(big,info_for(big,2,{0,1}));assert(hi.weight==UINT32_MAX);
    assert(hi.threshold>=UINT32_MAX/2);assert(large.stats().threshold_probes<=6);
    std::vector<unsigned> uneven={1,100,100,1};auto small_info=info_for(uneven,2,{0,1});
    Planner coarse(top2,{0,100,100,0},"F100");auto coarse_choice=coarse.choose(uneven,small_info);
    assert(coarse_choice.threshold==64 && coarse_choice.weight==100);
    // F001 打开残余压力重排序：在完全均匀的支持图上，交换行会得到
    // 交叉匹配 (1,0)，说明排序/平局规则确实生效且可复现。
    std::vector<unsigned> flat={100,100,100,100};auto flat_info=info_for(flat,2,{0,1});
    Planner ordered(top2,{0,100,100,0},"F001");
    assert(ordered.choose(flat,flat_info).matching==std::vector<int>({1,0}));

    // 随机性质测试：n∈{3,6,12}、每 server 2 GPU，比较扫描方法族与三个参考实现。
    // 这里同时让逐 GPU“真实剩余”略小于 server 级容量（-u），模拟 balance 后
    // 可能只剩部分 GPU 有余量的情形，验证虚拟补齐不会被误扣。
    std::mt19937 rng(291029);
    for(unsigned n:{3U,6U,12U})for(unsigned trial=0;trial<8;++trial){
        unsigned g=2;std::vector<int> dc(n);for(unsigned s=0;s<n;++s)dc[s]=s*3/n;
        Topology top(n,g,dc,400,400,100,50);std::vector<unsigned> values(n*n);
        std::vector<uint64_t> actual(n*n*g);
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d){values[s*n+d]=s==d?0:3+rng()%3000;
            for(unsigned u=0;u<g;++u)actual[(s*n+d)*g+u]=s==d?0:values[s*n+d]-u;}
        // 三个参考：
        //   native  —— 核心 Birkhoff 分解，对应 F000；
        //   bounded —— topology 的缓存打分变体（link-cost），对应 F010；
        //   exact   —— topology 的精确 max-min，对应 maxmin。
        // 后两者都通过同一个 decompose_with 扩展点工作。
        Matrix service(values.data(),n);FastAll2All native(&service,g,ETHER400,H100,1e-6);
        native.to_scaled_doubly_stochastic_matrix();native.decompose();
        native_topology::set_variant("bounded");
        Matrix oldservice(values.data(),n);native_topology::CachedPlanner bounded(top,actual,"link-cost");auto old=bounded.decompose(oldservice);
        Matrix exactservice(values.data(),n);native_topology::Planner exact(top,actual,"maxmin");auto want_exact=exact.decompose(exactservice);
        for(const std::string& method:{"F000","F001","F010","F011","F100","F101","F110","F111","maxmin"}){
            Matrix matrix(values.data(),n);FastAll2All app(&matrix,g,ETHER400,H100,1e-6);
            Planner planner(top,actual,method);planner.decompose_into(app);
            // 等价策略必须逐阶段复现参考实现；通用不变量对每个方法都成立。
            if(method=="F000")equal(native.p_sets,app.p_sets);
            if(method=="F010")equal(old,app.p_sets);
            if(method=="maxmin")equal(want_exact,app.p_sets);
            assert(app.verify_decomposition());assert(planner.remaining_inter_bytes()==0);
            assert(app.p_sets.size()<=n*n);assert(planner.stats().decomposition_runs==1);
            // L 每阶段最多尝试 cycle_budget(=2) 次，因此总测试数受阶段数限制。
            assert(planner.stats().cycle_trials<=2*app.p_sets.size());
            for(auto c:planner.stats().decisions){assert(c.weight>0 && c.weight>=c.threshold);
                if(method[0]=='F' && method[1]=='1')assert(c.probes<=6); // G 的二分探测上限
                assert(c.weight>=c.seed_weight);}                        // 修补不得降低系数
        }
    }
    // 空矩阵：不产生阶段，剩余真实字节为 0。
    Matrix zeros(3);FastAll2All empty_app(&zeros,1,ETHER400,H100,1e-6);
    Planner empty_planner(top3,std::vector<uint64_t>(9),"F111");empty_planner.decompose_into(empty_app);
    assert(empty_app.p_sets.empty());assert(empty_planner.remaining_inter_bytes()==0);
    // One real local-DC edge plus padding: only original real bytes survive.
    // 只有一个真实本地流（0->1, 17 单位），双随机化会补出大量虚拟容量；
    // 最终计划必须只承载 17 单位真实字节，且逐 GPU 剩余清零。
    std::vector<unsigned> one(36);one[1]=17;Matrix matrix(one.data(),6);
    FastAll2All app(&matrix,1,ETHER400,H100,1e-6);std::vector<uint64_t> one_real(one.begin(),one.end());
    Planner only_local(top6,one_real,"F111");only_local.decompose_into(app);
    assert(only_local.remaining_inter_bytes()==0);assert(app.verify_decomposition());
    std::cout<<"G/L/R native/bounded/maxmin identity, support/real conservation, coefficient guard, resource pressure and uint32 tests passed\n";
}

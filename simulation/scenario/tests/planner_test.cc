// scenario 构建下构造式匹配（A1/A2）的测试：
//   - A1/A2 的构造预算分别为 1/2；
//   - 全支持、均匀系数的 6×6 矩阵上，A1 得到一个确定的 LAN 优先匹配；
//   - 随机稀疏支持图（含 0 单元）上，A1/A2 仍守恒、系数等于精确 q*、
//     构造次数不超过预算。
#include "planner.h"
#include <cassert>
#include <iostream>
#include <numeric>
#include <random>
using namespace native_scan;
using native_topology::Topology;
// 构造 choose() 需要的 hungarian_info：行/列双向 matching + >0 支持边。
static hungarian_info_t info_for(const std::vector<unsigned>& a,unsigned n,const std::vector<int>& m){
    hungarian_info_t info;info.matching.assign(2*n,-1);info.row_to_col.resize(n);
    for(unsigned s=0;s<n;++s){info.matching[s]=n+m[s];info.matching[n+m[s]]=s;for(unsigned d=0;d<n;++d)if(a[s*n+d])info.row_to_col[s].insert(n+d);}
    return info;
}
int main(){
    // 方法名到构造预算的解析。
    assert(Options::parse("A1").constructive_budget==1);assert(Options::parse("A2").constructive_budget==2);
    // 全 100 支持（对角线除外）：A1 只重建一次，得到按本地优先的交换匹配。
    Topology t(6,1,{0,0,1,1,2,2});std::vector<unsigned> a(36,100);for(unsigned s=0;s<6;++s)a[s*6+s]=0;
    std::vector<uint64_t> real(a.begin(),a.end());auto info=info_for(a,6,{2,3,4,5,0,1});
    Planner p(t,real,"A1");auto d=p.choose(a,info);assert(d.matching==std::vector<int>({1,0,3,2,5,4}));
    assert(d.weight==100);assert(d.constructions==1);assert(p.stats().construction_trials==1);
    // 随机稀疏支持：约 1/4 的 server 对为 0，逐 GPU 真实剩余再减 u（非负）。
    std::mt19937 rng(302901);
    for(unsigned n:{3U,6U,12U,24U})for(unsigned trial=0;trial<4;++trial){
        unsigned g=2;std::vector<int> dc(n);for(unsigned s=0;s<n;++s)dc[s]=s*3/n;
        Topology top(n,g,dc);std::vector<unsigned> values(n*n);std::vector<uint64_t> rows(n*n*g);
        for(unsigned s=0;s<n;++s)for(unsigned z=0;z<n;++z)if(s!=z){values[s*n+z]=(rng()%4)?1+rng()%5000:0;
            for(unsigned u=0;u<g;++u)rows[(s*n+z)*g+u]=values[s*n+z]?values[s*n+z]-u:0;}
        for(const std::string& method:{"A1","A2"}){
            Matrix service(values.data(),n);FastAll2All app(&service,g,ETHER400,H100,1e-6);Planner planner(top,rows,method);planner.decompose_into(app);
            // 通用不变量：分解可还原、真实剩余清零、阶段数有界。
            assert(app.verify_decomposition());assert(planner.remaining_inter_bytes()==0);assert(app.p_sets.size()<=n*n);
            // 构造次数最多为预算 × 阶段数；每阶段不超过预算。
            assert(planner.stats().construction_trials<=Options::parse(method).constructive_budget*app.p_sets.size());
            for(const auto& decision:planner.stats().decisions){assert(decision.weight==decision.exact_weight);assert(decision.weight>=decision.seed_weight);
                assert(decision.constructions<=Options::parse(method).constructive_budget);}
        }
    }
    std::cout<<"Constructive augmentation: literal LAN choice, sparse support, exact coefficients and payload conservation passed\n";
}

// ordering 构建测试：
//   1. support_summary 用 Tarjan SCC 区分“唯一完美匹配”“可交替换边”和
//      “只有虚拟容量可动”；单条额外边不构成环，不算自由度；
//   2. constructive_order 的四种行排序在给定图上逐一核对字面结果；
//   3. row_priorities 随 WAN 方向带宽变化：瓶颈方向上的行压力更高；
//   4. A1D/A1P/A1DP 在 45 个稀疏随机矩阵上保持守恒且每阶段构造一次。
#include "planner.h"
#include <cassert>
#include <iostream>
#include <numeric>
#include <random>
using namespace native_scan;
int main(){
    // 3×3 矩阵：匹配 (0->1,1->2,2->0) 系数 8。
    // q=8 时支持图只有匹配边 -> 唯一、无自由度；
    // q=2 时支持图含 6 条边，三条匹配边可沿交替环移动，且都涉及真实剩余。
    std::vector<unsigned> a={0,8,2,2,0,8,8,2,0};std::vector<int> m={1,2,0};std::vector<uint64_t> real(a.begin(),a.end());
    auto unique=support_summary(a,3,8,m,real,1);assert(unique.edges==3&&unique.movable_rows==0&&unique.flexible_edges==0);
    auto cycle=support_summary(a,3,2,m,real,1);assert(cycle.edges==6&&cycle.movable_rows==3&&cycle.flexible_edges==3&&cycle.real_movable_rows==3);
    // 把所有真实剩余清零后，相同的可动行仍是“虚拟可动”，不能算真实自由度。
    std::fill(real.begin(),real.end(),0);assert(support_summary(a,3,2,m,real,1).real_movable_rows==0);
    real[1]=8;assert(support_summary(a,3,2,m,real,1).real_movable_rows==1);
    // 三角支持图（0->1,1->0,0->? ...）：多出一条边但没有交替环，movable_rows=0。
    auto triangular=support_summary({5,5,0,0,5,0,0,0,5},3,5,{0,1,2},std::vector<uint64_t>(9),1);
    assert(triangular.edges==4&&triangular.movable_rows==0); // Extra edge alone is not an alternating cycle.
    // 每行支持度/压力不同，四种排序策略应给出不同的、可预测的构造顺序。
    //   order=0 自然；1 稀缺度；2 压力；3 先度后压力。
    std::vector<unsigned> scarce={1,1,1,1,1,0,0,0,0,1,1,0,0,0,0,1};std::vector<double> pressure={10,1,9,5};
    assert(constructive_order(scarce,4,1,{},0)==std::vector<unsigned>({0,1,2,3}));
    assert(constructive_order(scarce,4,1,{},1)==std::vector<unsigned>({1,3,2,0}));
    assert(constructive_order(scarce,4,1,pressure,2)==std::vector<unsigned>({0,2,3,1}));
    assert(constructive_order(scarce,4,1,pressure,3)==std::vector<unsigned>({3,1,2,0}));
    // 两个真实跨 DC 流 0->2 与 2->0 共享 WAN，但方向不同：
    // 当 wan:0:1 只有 25Gbps 时，行 0 的压力应高于行 2；反转带宽后相反。
    std::vector<unsigned> full(16,1);for(unsigned i=0;i<4;++i)full[i*4+i]=0;
    std::vector<uint64_t> demand(16);demand[2]=100;demand[8]=100;
    native_topology::Topology t(4,1,{0,0,1,1});t.bandwidths["wan:0:1"]=25;
    auto first=ResourceCache(t).row_priorities(full,1,demand);assert(first[0]>first[2]&&first[1]==0&&first[3]==0);
    t.bandwidths["wan:0:1"]=100;t.bandwidths["wan:1:0"]=25;
    auto second=ResourceCache(t).row_priorities(full,1,demand);assert(second[2]>second[0]);
    // 随机稀疏矩阵上验证完整分解：n 最大 48（阶段数上界 n²）。
    std::mt19937 rng(640001);
    for(unsigned n:{3U,6U,12U,24U,48U})for(unsigned k=0;k<3;++k){unsigned g=2;
        std::vector<int> dc(n);std::vector<unsigned> values(n*n);std::vector<uint64_t> rows(n*n*g);
        for(unsigned s=0;s<n;++s){dc[s]=s*3/n;for(unsigned d=0;d<n;++d)if(s!=d){values[s*n+d]=(rng()%4)?2+rng()%500:0;
            for(unsigned u=0;u<g;++u)rows[(s*n+d)*g+u]=values[s*n+d]?values[s*n+d]-u:0;}}
        native_topology::Topology top(n,g,dc);
        for(const std::string method:{"A1D","A1P","A1DP"}){
            Matrix matrix(values.data(),n);FastAll2All app(&matrix,g,ETHER400,H100,1e-6);Planner planner(top,rows,method);planner.decompose_into(app);
            assert(app.verify_decomposition()&&planner.remaining_inter_bytes()==0);assert(planner.stats().construction_trials==app.p_sets.size());
            for(const auto& d:planner.stats().decisions)assert(d.constructions==1&&d.weight==d.exact_weight&&d.weight>=d.seed_weight);
        }
    }
    std::cout<<"Support freedom, virtual distinction, literal row orders, capacity-sensitive pressure and45 sparse decompositions passed\n";
}

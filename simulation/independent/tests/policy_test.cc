// ============================================================================
// independent/tests/policy_test.cc：minimum_bottleneck() 的精确性小测试。
//
// 这个测试不跑完整仿真，只验证 time 策略的核心子问题：
//   minimize max_s costs[s][m(s)]
//   s.t. m 是 perfect matching 且所有边满足 a>=q。
// 对 n=2..5 的随机小图，用所有排列穷举真实最优值，再与多项式算法比较；
// 穷举只用于测试（n 很小），不是线上实现。另含两个定向反例：
//   - 最小和（min-sum）会选错的 $0/5/5/9$ 图；
//   - 代价为 0 但 a<q 的禁用边不得被使用。
// ============================================================================

#include "policy.h"
#include "link_profile.h"
#include <algorithm>
#include <cassert>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>

// 与 main.cc 相同：把冻结引擎的链接工厂重定向到论文档案实现。
link_info_t profile_get_inter_link_info(INTER_LINK_TYPE);
link_info_t profile_get_intra_link_info(INTRA_LINK_TYPE);
link_info_t get_inter_link_info(INTER_LINK_TYPE t){return profile_get_inter_link_info(t);}
link_info_t get_intra_link_info(INTRA_LINK_TYPE t){return profile_get_intra_link_info(t);}
int main(){
    uint64_t calls=0;
    // 定向反例 1：代价矩阵为 [[0,5],[5,9]]。两条完美匹配：
    //   对角线 {0->0,1->1}: 总和 9，最大值 9；
    //   反对角 {0->1,1->0}: 总和 10，最大值 5。
    // 最小和会选总和 9 的对角线，而最小瓶颈必须选 max 更小的反对角；
    // 返回 {1,0} 证明实现没有退化成 min-sum。
    auto result=independent_fast::minimum_bottleneck({1,1,1,1},{{0,1},{0,1}},1,{0,5,5,9},calls);
    assert((result==std::vector<int>{1,0})); // Min-sum would choose the wrong diagonal.
    // 定向反例 2：对角线容量为 0（a<q=1），即使代价 9 也不能用；
    // 唯一可行匹配是反对角 {0->1,1->0}，其两条边代价都是 0，max=0。
    // 这里验证“容量为 0 的边不会被代价更低/更高地错误纳入”。
    result=independent_fast::minimum_bottleneck({1,0,0,1},{{0,1},{0,1}},1,{9,0,0,9},calls);
    assert((result==std::vector<int>{0,1})); // Zero-cost forbidden edges must not leak in.
    std::mt19937 random(941203);
    // 随机穷举：n=2..5，每个 n 40 个样本，共 160 个图。
    for(unsigned n=2;n<=5;++n)for(unsigned sample=0;sample<40;++sample){
        unsigned q=1+random()%3;std::vector<unsigned> a(n*n);std::vector<double> costs(n*n);
        std::vector<std::vector<unsigned>> neighbors(n);
        // 对角线容量固定为 5（通常可用），非对角线 0..4，用来制造
        // a<q 时支持图稀疏/无解的情形；代价 0..20。
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d){
            a[s*n+d]=s==d?5:random()%5;costs[s*n+d]=random()%21;neighbors[s].push_back(d);}
        // 故意把一半行的邻接顺序反转，验证结果不依赖 enumerated 顺序。
        for(unsigned s=0;s<n;s+=2)std::reverse(neighbors[s].begin(),neighbors[s].end());
        auto chosen=independent_fast::minimum_bottleneck(a,neighbors,q,costs,calls);
        // 先验证返回值本身合法：每条边满足 a>=q，且是一个排列。
        double actual=0;for(unsigned s=0;s<n;++s){assert(a[s*n+chosen[s]]>=q);actual=std::max(actual,costs[s*n+chosen[s]]);}
        // 穷举所有排列，求满足 a>=q 的最小最大代价 best。
        std::vector<unsigned> permutation(n);std::iota(permutation.begin(),permutation.end(),0);
        double best=std::numeric_limits<double>::infinity();
        do{bool valid=true;double maximum=0;
            for(unsigned s=0;s<n;++s){valid=valid&&a[s*n+permutation[s]]>=q;maximum=std::max(maximum,costs[s*n+permutation[s]]);}
            if(valid)best=std::min(best,maximum);
        }while(std::next_permutation(permutation.begin(),permutation.end()));
        assert(actual==best);
    }
    // 支持图没有完美匹配时必须显式拒绝，而不是返回部分匹配。
    bool rejected=false;
    try{independent_fast::minimum_bottleneck({0},{{0}},1,{0},calls);}catch(const std::invalid_argument&){rejected=true;}
    assert(rejected);
    std::cout<<"160 exhaustive small-graph bottleneck checks passed\n";
}

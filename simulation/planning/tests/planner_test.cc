// planning 构建测试：把“规划工作区是否可复用”当作一等性质。
//   - 全局重载 operator new，临时统计分配次数；
//   - balanced_matching 的 optimized=true 版本必须与参考版结果相同，
//     且暖机后每次调用只做常数次分配（工作区复用）；
//   - maxmin-ref/maxmin-opt 的阈值搜索次数必须相同（只优化构造工作区）；
//   - 多跳旋转、失败重试、随机图上结果与代价一致；
//   - SPM 的掩码最大权匹配与暴力枚举对照；静态路径效用公式手算。
#include "planner.h"
#include <cassert>
#include <cstdlib>
#include <iostream>
#include <new>
#include <numeric>
#include <random>
#include <algorithm>
#include <cmath>

// 分配计数开关：只包裹 new/delete，不影响 STL 容器语义。
static bool count_allocations=false;
static size_t allocations=0;
void* operator new(std::size_t size){if(count_allocations)++allocations;if(void* p=std::malloc(size?size:1))return p;throw std::bad_alloc();}
void operator delete(void* p)noexcept{std::free(p);}
void* operator new[](std::size_t size){return ::operator new(size);}
void operator delete[](void* p)noexcept{::operator delete(p);}
using namespace native_scan;

int main(){
    native_topology::Topology t(6,1,{0,0,1,1,2,2});ResourceCache cache(t);
    // 完全图（对角线除外），所有边容量 100、真实剩余 100。
    std::vector<unsigned> a(36,100),order(6);std::vector<std::vector<unsigned>> neighbors(6);
    std::iota(order.begin(),order.end(),0);
    for(unsigned s=0;s<6;++s){a[s*6+s]=0;for(unsigned d=0;d<6;++d)if(s!=d)neighbors[s].push_back(d);}
    std::vector<uint64_t> rows(a.begin(),a.end());std::vector<int> ref,fast;
    // 先暖机，确保工作区容器已分配；随后 optimized 版应复用它们。
    assert(cache.balanced_matching(a,neighbors,100,rows,order,ref,false));
    assert(cache.balanced_matching(a,neighbors,100,rows,order,fast,true));assert(ref==fast);
    allocations=0;count_allocations=true;bool ok=cache.balanced_matching(a,neighbors,100,rows,order,ref,false);count_allocations=false;assert(ok);size_t before=allocations;
    allocations=0;count_allocations=true;ok=cache.balanced_matching(a,neighbors,100,rows,order,fast,true);count_allocations=false;assert(ok);size_t after=allocations;
    std::cout<<"Matcher allocations after warmup: "<<before<<" -> "<<after<<std::endl;
    assert(after<=4&&after*2<before); // Catches per-DFS vector growth/stable_sort allocations.
    // The threshold-search "fast" kernel regressed on the isolated Zipf
    // control. Both implementations must now use the reference search;
    // only the constructive workspace is optimized.
    // maxmin-ref 与 maxmin-opt 只允许在构造工作区上不同，阈值搜索必须完全一致；
    // 这里同时核对分配次数完全相同，防止“偷偷”换了搜索内核。
    hungarian_info_t info;info.matching.assign(12,-1);info.row_to_col.resize(6);
    for(unsigned s=0;s<6;++s){unsigned d=s^1;info.matching[s]=6+d;info.matching[6+d]=s;
        for(unsigned v:neighbors[s])info.row_to_col[s].insert(6+v);}
    Planner reference_search(t,rows,"maxmin-ref"),optimized_search(t,rows,"maxmin-opt");
    allocations=0;count_allocations=true;auto rd=reference_search.choose(a,info);count_allocations=false;before=allocations;
    allocations=0;count_allocations=true;auto od=optimized_search.choose(a,info);count_allocations=false;after=allocations;
    assert(rd.matching==od.matching&&rd.weight==od.weight);
    std::cout<<"Threshold control allocations: "<<before<<" / "<<after<<std::endl;
    assert(before==after);

    // 三个 4×4 支持图分别测试：单环、带弦的环（多跳位移）、不可行图。
    // 参考版与 optimized 版在成功/失败两种情况下都必须给出相同结果。
    native_topology::Topology t4(4,1,{0,0,1,1});ResourceCache c4(t4);std::vector<unsigned> o4={0,1,2,3};std::vector<uint64_t> zeros(16);
    for(const std::vector<std::vector<unsigned>>& graph:{std::vector<std::vector<unsigned>>{{0,1},{1,2},{2,3},{0}},
                                                        std::vector<std::vector<unsigned>>{{0,1,3},{1},{2},{0}},
                                                        std::vector<std::vector<unsigned>>{{0},{0},{2},{3}}}){
        std::vector<unsigned> mask(16);for(unsigned s=0;s<4;++s)for(unsigned d:graph[s])mask[s*4+d]=1;
        bool x=c4.balanced_matching(mask,graph,1,zeros,o4,ref,false),y=c4.balanced_matching(mask,graph,1,zeros,o4,fast,true);assert(x==y&&ref==fast);
        if(x)assert(ref==std::vector<int>({1,2,3,0})||ref==std::vector<int>({3,1,2,0}));
    }
    // 随机图上的等价性：30 个矩阵 × 两个阈值 q，比较匹配、score 的 us/bytes。
    std::mt19937 rng(760001);
    for(unsigned n:{3U,6U,12U,24U,48U})for(unsigned trial=0;trial<3;++trial){
        unsigned g=2;std::vector<int> dc(n);std::vector<unsigned> residual(n*n),natural(n);std::vector<uint64_t> real(n*n*g);std::vector<std::vector<unsigned>> graph(n);
        std::iota(natural.begin(),natural.end(),0);
        for(unsigned s=0;s<n;++s){dc[s]=s*3/n;for(unsigned d=0;d<n;++d){residual[s*n+d]=s==d?1000:(rng()%4?1+rng()%100:0);
            if(residual[s*n+d])graph[s].push_back(d);if(s!=d)for(unsigned u=0;u<g;++u)real[(s*n+d)*g+u]=rng()%101;}}
        native_topology::Topology top(n,g,dc);ResourceCache current(top);
        for(unsigned q:{1U,37U}){
            bool x=current.balanced_matching(residual,graph,q,real,natural,ref,false),y=current.balanced_matching(residual,graph,q,real,natural,fast,true);
            assert(x==y&&ref==fast);auto r=current.score(ref,q,real),f=current.score(fast,q,real);assert(r.us==f.us&&r.bytes==f.bytes);
        }
    }
    std::cout<<"Equivalent buffer reuse: stable ties, multi-hop displacement, failure reuse and30 random kernel comparisons passed\n";
    // SPM：掩码最大权匹配 vs 暴力枚举所有 n! 个置换。
    // n=3/4 各 40 组，mask>=2 才可用；同时检查结果可复现。
    for(unsigned n:{3U,4U})for(unsigned trial=0;trial<40;++trial){
        std::vector<unsigned> mask(n*n);std::vector<double> utility(n*n);std::vector<int> p(n);std::iota(p.begin(),p.end(),0);
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d){mask[s*n+d]=s==d?2:rng()%3;utility[s*n+d]=rng()%7;}
        double best=-1;do{bool valid=true;double value=0;for(unsigned s=0;s<n;++s){valid=valid&&mask[s*n+p[s]]>=2;value+=utility[s*n+p[s]];}if(valid)best=std::max(best,value);}while(std::next_permutation(p.begin(),p.end()));
        auto match=maximum_weight_matching(mask,n,2,utility);double value=0;std::vector<bool> seen(n);
        for(unsigned s=0;s<n;++s){assert(match[s]>=0&&match[s]<int(n)&&!seen[match[s]]&&mask[s*n+match[s]]>=2);seen[match[s]]=true;value+=utility[s*n+match[s]];}
        assert(value==best&&match==maximum_weight_matching(mask,n,2,utility));
    }
    // 掩码下不存在完美匹配时必须抛 runtime_error，而不是返回部分匹配。
    bool rejected=false;try{maximum_weight_matching({1,0,1,0},2,1,{1,2,3,4});}catch(const std::runtime_error&){rejected=true;}assert(rejected);
    // SPM 的静态边效用 = 真实字节 / 单流拓扑时间；对角线不产生效用。
    auto utility=cache.individual_utilities(100,rows);
    assert(utility[0]==0); // Dummy diagonal never earns utility.
    for(unsigned s=0;s<6;++s)for(unsigned d=0;d<6;++d)if(s!=d){
        auto cost=t.evaluate({{s,d,100}});assert(std::abs(utility[s*6+d]-100/cost.total_us)<1e-12);}
    assert(c4.individual_utilities(100,zeros)==std::vector<double>(16));
    assert(Options::parse("SPM-opt").static_path&&Options::parse("SPM-opt").optimized);
    std::cout<<"Masked maximum-weight assignment:80 brute-force objective checks; static real-byte path utility passed\n";
}

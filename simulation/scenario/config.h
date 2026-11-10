#pragma once
#include "model.h"
#include <cmath>
#include <set>
#include <sstream>
#include <stdexcept>

// ============================================================================
// scenario/ 的严格配置解析。
//
// scan 的 FAST_SCENARIO_SCAN 构建允许用命令行覆盖拓扑：
//   DC_CSV       每个 server 的 DC 标签，逗号分隔，长度必须等于 n；
//   SERVER_BW    每个 server 的出口/入口带宽（Gbps）；
//   GATEWAY_BW   每个 DC 的 gateway 带宽；
//   WAN_BW       对称 WAN 带宽；
//   PROFILE      uniform（保持默认）或 asymmetric（按 DC 对差异缩放）。
// 这些函数只校验并修改传入的 Topology，不触碰调度逻辑。
// ============================================================================
namespace native_scenario {
// 解析一个正的有限带宽值；拒绝空串、符号、尾随字符、0/负值/NaN。
inline double capacity(const std::string& s){
    size_t used=0;double value=std::stod(s,&used);
    if(used!=s.size()||!std::isfinite(value)||value<=0)throw std::invalid_argument("invalid capacity");return value;
}
// 解析 DC 标签 CSV。要求：非空、不以逗号结尾、每项纯数字、
// 0 <= label < n、项数恰好为 n（每个 server 一个标签）。
inline std::vector<int> labels(const std::string& csv,unsigned n){
    std::vector<int> dc;std::stringstream stream(csv);std::string item;
    if(csv.empty()||csv.back()==',')throw std::invalid_argument("invalid DC list");
    while(std::getline(stream,item,',')){
        if(item.empty()||item.find_first_not_of("0123456789")!=std::string::npos)throw std::invalid_argument("invalid DC label");
        auto label=std::stoull(item);if(label>=n)throw std::invalid_argument("DC label range");dc.push_back(label);
    }
    if(dc.size()!=n)throw std::invalid_argument("DC list dimension");return dc;
}
// 按 profile 调整 WAN 带宽：
//   uniform    —— 直接返回，使用构造 Topology 时的对称带宽；
//   asymmetric —— 对每个无序 DC 对取 .5/1/2 的周期模式，
//                 再除以所有 DC 对的均值，使平均带宽仍为 wb。
// 只修改 wan:a:b 两个方向；server/gateway 带宽保持不变。
// 该函数直接操作 map 元素（at 不存在会抛异常），因此必须在 Topology 构造后调用。
inline void capacities(native_topology::Topology& top,const std::string& profile,double wb){
    if(profile=="uniform")return;
    if(profile!="asymmetric")throw std::invalid_argument("unknown capacity profile");
    std::set<int> groups(top.dc.begin(),top.dc.end());double sum=0;unsigned count=0;const double pattern[]={.5,1,2};
    for(int a:groups)for(int b:groups)if(a<b){sum+=pattern[(a+b)%3];++count;}
    if(!count)throw std::invalid_argument("asymmetric requires multiple DCs");
    double mean=sum/count;
    for(int a:groups)for(int b:groups)if(a!=b)top.bandwidths.at("wan:"+std::to_string(a)+":"+std::to_string(b))=wb*pattern[(a+b)%3]/mean;
}
}

#pragma once
#include "model.h"
#include <cmath>
#include <set>
#include <sstream>
#include <stdexcept>

namespace native_scenario {
inline double capacity(const std::string& s){
    size_t used=0;double value=std::stod(s,&used);
    if(used!=s.size()||!std::isfinite(value)||value<=0)throw std::invalid_argument("invalid capacity");return value;
}
inline std::vector<int> labels(const std::string& csv,unsigned n){
    std::vector<int> dc;std::stringstream stream(csv);std::string item;
    if(csv.empty()||csv.back()==',')throw std::invalid_argument("invalid DC list");
    while(std::getline(stream,item,',')){
        if(item.empty()||item.find_first_not_of("0123456789")!=std::string::npos)throw std::invalid_argument("invalid DC label");
        auto label=std::stoull(item);if(label>=n)throw std::invalid_argument("DC label range");dc.push_back(label);
    }
    if(dc.size()!=n)throw std::invalid_argument("DC list dimension");return dc;
}
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

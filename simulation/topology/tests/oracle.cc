#include "global.h"
#include <iomanip>
#include <memory>
#include <stdexcept>

int main(int argc,char** argv) {
  if(argc!=3)return 2;
  unsigned n=std::stoul(argv[1]),g=std::stoul(argv[2]),r=n*g;
  std::vector<unsigned> data(r*r);
  for(auto& v:data)if(!(std::cin>>v))return 3;
  std::vector<LocalScheduler*> locals;
  for(unsigned s=0;s<n;++s)locals.push_back(new LocalScheduler(data.data()+s*g*r,g,n,s,H100));
  GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,1e-6);
  auto result=scheduler.pipeline2(1e-6);
  std::cout<<std::setprecision(17)<<result.t<<" "<<result.ratio<<" "
    <<spread_out_baseline(data.data(),n,g,get_inter_link_info(ETHER400),get_intra_link_info(H100),1e-6)<<"\n";
  for(auto p:locals)delete p;
}

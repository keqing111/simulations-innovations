// 独立“预言机”：不经过任何 topology/scan 扩展，直接运行核心 pipeline2，
// 输出 FAST 模型时间、ratio 和 Spread-out 基线。
// topology/tests 用它作为对照，确认钩子实验没有改变核心数值语义。
// 用法：oracle SERVERS GPUS < matrix；输出一行三个数。
#include "global.h"
#include <iomanip>
#include <memory>
#include <stdexcept>

int main(int argc,char** argv) {
  if(argc!=3)return 2;
  unsigned n=std::stoul(argv[1]),g=std::stoul(argv[2]),r=n*g;
  std::vector<unsigned> data(r*r);
  for(auto& v:data)if(!(std::cin>>v))return 3;
  // 每个 LocalScheduler 负责自己的 g 行 GPU 子矩阵；不复制数据，直接指向原矩阵。
  std::vector<LocalScheduler*> locals;
  for(unsigned s=0;s<n;++s)locals.push_back(new LocalScheduler(data.data()+s*g*r,g,n,s,H100));
  GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,1e-6);
  auto result=scheduler.pipeline2(1e-6);
  std::cout<<std::setprecision(17)<<result.t<<" "<<result.ratio<<" "
    <<spread_out_baseline(data.data(),n,g,get_inter_link_info(ETHER400),get_intra_link_info(H100),1e-6)<<"\n";
  for(auto p:locals)delete p;
}

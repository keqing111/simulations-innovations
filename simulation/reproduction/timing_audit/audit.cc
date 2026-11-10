// ============================================================================
// timing_audit：独立复算“某个具体阶段/拓扑”的模型时间，用于审计 Python
// 侧或论文表格中的数字，不涉及 FAST 的匹配/分解。
//
// 两种模式：
//   local  model alpha beta MBpu        —— stdin 提供 8×8 单 server 矩阵，
//                                          直接调用 server 内传输回调；
//   global model servers matrix.bin     —— 读取 n 个 server 的二进制矩阵，
//                                          调用 pipeline1/2/3 的模型时间。
//
// local 的 model ∈ spread_out | full_mesh | two_ring | hybrid_cube；
// global 的 model ∈ pipeline1 | pipeline2 | spread_out | full_mesh |
//                   two_ring | hybrid_cube（pipeline3 使用对应回调）。
// 这里 g 固定为 8，与论文中的单机 8 GPU 设定一致。
// ============================================================================
#include "global.h"
#include "link_profile.h"
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <memory>
#include <stdexcept>

// 把命令行名字映射到核心库的 server 内传输拓扑函数；未知名字直接拒绝。
static intra_transfer_topo_fn callback(const std::string& name){
  if(name=="spread_out")return &spread_out;
  if(name=="full_mesh")return &intra_transfer_full_mesh;
  if(name=="two_ring")return &intra_transfer_2ring;
  if(name=="hybrid_cube")return &intra_transfer_hybrid_cude_mesh;
  throw std::invalid_argument("unknown native callback");
}
int main(int argc,char** argv){
  try{
    if(argc<3)throw std::invalid_argument("local|global model ...");
    std::string operation=argv[1],model=argv[2];
    double time=0,total=0;unsigned r=8;
    if(operation=="local"){
      // local：固定 8×8 矩阵，alpha/beta/MBpu 全部显式给出，不读取链路 profile。
      if(argc!=6)throw std::invalid_argument("local model alpha beta MBpu; stdin 8x8 matrix");
      double alpha=std::stod(argv[3]),beta=std::stod(argv[4]),mbpu=std::stod(argv[5]);
      if(!std::isfinite(alpha)||!std::isfinite(beta)||!std::isfinite(mbpu)||alpha<0||beta<=0||mbpu<=0)throw std::invalid_argument("link range");
      std::vector<unsigned> matrix(64);uint64_t value;
      for(auto& v:matrix){if(!(std::cin>>value)||value>9999)throw std::invalid_argument("64 cells in [0,9999] required");v=value;}
      std::string trailing;if(std::cin>>trailing)throw std::invalid_argument("trailing cells");
      // link_info_t{MB, alpha, beta}；直接调用拓扑回调得到模型时间（us）。
      time=callback(model)(matrix.data(),8,link_info_t{0,alpha,beta},mbpu);
    }else if(operation=="global"){
      // global：server 数可变，g 固定 8；矩阵按小端 uint32 读取。
      if(argc!=5)throw std::invalid_argument("global model servers matrix.bin");
      unsigned n=std::stoul(argv[3]),g=8;if(n<2||n>40)throw std::invalid_argument("server range");r=n*g;
      std::ifstream stream(argv[4],std::ios::binary);std::vector<unsigned> data(r*r);
      for(auto& v:data){unsigned char b[4];if(!stream.read(reinterpret_cast<char*>(b),4))throw std::invalid_argument("truncated matrix");v=uint32_t(b[0])|(uint32_t(b[1])<<8)|(uint32_t(b[2])<<16)|(uint32_t(b[3])<<24);if(v>9999)throw std::invalid_argument("cell range");total+=v;}
      if(stream.peek()!=EOF)throw std::invalid_argument("trailing data");
      for(unsigned i=0;i<r;++i)if(data[i*r+i])throw std::invalid_argument("diagonal");
      // 与 reproduction 主驱动相同的论文 profile。
      configure_link_profile("paper",400,3600,1);
      std::vector<std::unique_ptr<LocalScheduler>> owners;std::vector<LocalScheduler*> locals;
      for(unsigned s=0;s<n;++s){owners.emplace_back(new LocalScheduler(data.data()+s*g*r,g,n,s,H100));locals.push_back(owners.back().get());}
      GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,.01);
      // 统一使用 MBpu=.01（百分之一 MB），与历史论文脚本一致。
      if(model=="pipeline1")time=scheduler.pipeline(.01).t;
      else if(model=="pipeline2")time=scheduler.pipeline2(.01).t;
      else time=scheduler.pipeline3(.01,callback(model)).t;
      total*=.01; // decimal MB
    }else throw std::invalid_argument("operation");
    // 输出的 algbw 与 reproduction/driver 相同：total(MB)*1000/(GPU数*us)。
    if(!std::isfinite(time)||time<0)throw std::runtime_error("invalid native time");
    std::cout<<std::setprecision(17)<<"{\"time_us\":"<<time<<",\"algbw_GB_s\":"<<(total?total*1000/(r*time):0)<<"}\n";
  }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}

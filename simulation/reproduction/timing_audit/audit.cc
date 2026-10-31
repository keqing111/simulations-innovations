#include "global.h"
#include "link_profile.h"
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <memory>
#include <stdexcept>

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
      if(argc!=6)throw std::invalid_argument("local model alpha beta MBpu; stdin 8x8 matrix");
      double alpha=std::stod(argv[3]),beta=std::stod(argv[4]),mbpu=std::stod(argv[5]);
      if(!std::isfinite(alpha)||!std::isfinite(beta)||!std::isfinite(mbpu)||alpha<0||beta<=0||mbpu<=0)throw std::invalid_argument("link range");
      std::vector<unsigned> matrix(64);uint64_t value;
      for(auto& v:matrix){if(!(std::cin>>value)||value>9999)throw std::invalid_argument("64 cells in [0,9999] required");v=value;}
      std::string trailing;if(std::cin>>trailing)throw std::invalid_argument("trailing cells");
      time=callback(model)(matrix.data(),8,link_info_t{0,alpha,beta},mbpu);
    }else if(operation=="global"){
      if(argc!=5)throw std::invalid_argument("global model servers matrix.bin");
      unsigned n=std::stoul(argv[3]),g=8;if(n<2||n>40)throw std::invalid_argument("server range");r=n*g;
      std::ifstream stream(argv[4],std::ios::binary);std::vector<unsigned> data(r*r);
      for(auto& v:data){unsigned char b[4];if(!stream.read(reinterpret_cast<char*>(b),4))throw std::invalid_argument("truncated matrix");v=uint32_t(b[0])|(uint32_t(b[1])<<8)|(uint32_t(b[2])<<16)|(uint32_t(b[3])<<24);if(v>9999)throw std::invalid_argument("cell range");total+=v;}
      if(stream.peek()!=EOF)throw std::invalid_argument("trailing data");
      for(unsigned i=0;i<r;++i)if(data[i*r+i])throw std::invalid_argument("diagonal");
      configure_link_profile("paper",400,3600,1);
      std::vector<std::unique_ptr<LocalScheduler>> owners;std::vector<LocalScheduler*> locals;
      for(unsigned s=0;s<n;++s){owners.emplace_back(new LocalScheduler(data.data()+s*g*r,g,n,s,H100));locals.push_back(owners.back().get());}
      GlobalScheduler scheduler(n,g,locals,ETHER400,H100,false,.01);
      if(model=="pipeline1")time=scheduler.pipeline(.01).t;
      else if(model=="pipeline2")time=scheduler.pipeline2(.01).t;
      else time=scheduler.pipeline3(.01,callback(model)).t;
      total*=.01; // decimal MB
    }else throw std::invalid_argument("operation");
    if(!std::isfinite(time)||time<0)throw std::runtime_error("invalid native time");
    std::cout<<std::setprecision(17)<<"{\"time_us\":"<<time<<",\"algbw_GB_s\":"<<(total?total*1000/(r*time):0)<<"}\n";
  }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}

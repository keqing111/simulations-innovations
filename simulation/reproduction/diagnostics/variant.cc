// Diagnostic entry points only; all scheduling implementations are pinned upstream.
#include "global.h"
#include "link_profile.h"
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <memory>
#include <stdexcept>

int main(int argc,char** argv){
  try{
    if(argc!=6)throw std::invalid_argument("n g MBpu variant matrix.bin");
    unsigned n=std::stoul(argv[1]),g=std::stoul(argv[2]),r=n*g;
    double mbpu=std::stod(argv[3]);std::string mode=argv[4];
    if(n<2||n>40||g<1||g>8||mbpu<=0||mbpu>1)throw std::invalid_argument("range");
    if(mode!="pipeline2"&&mode!="pipeline2_scaling"&&mode!="pipeline3_spread"&&mode!="pipeline3_mesh")throw std::invalid_argument("variant");
    std::ifstream stream(argv[5],std::ios::binary);std::vector<unsigned> data(r*r);uint64_t total=0;
    for(auto& v:data){unsigned char b[4];if(!stream.read(reinterpret_cast<char*>(b),4))throw std::runtime_error("matrix truncated");v=uint32_t(b[0])|(uint32_t(b[1])<<8)|(uint32_t(b[2])<<16)|(uint32_t(b[3])<<24);if(v>9999)throw std::invalid_argument("cell");total+=v;}
    if(stream.peek()!=EOF)throw std::invalid_argument("matrix trailing bytes");
    for(unsigned i=0;i<r;++i)if(data[i*r+i])throw std::invalid_argument("diagonal");
    configure_link_profile("paper",400,3600,1);
    std::vector<std::unique_ptr<LocalScheduler>> owners;std::vector<LocalScheduler*> locals;
    for(unsigned s=0;s<n;++s){owners.emplace_back(new LocalScheduler(data.data()+s*g*r,g,n,s,H100));locals.push_back(owners.back().get());}
    GlobalScheduler scheduler(n,g,locals,ETHER400,H100,mode=="pipeline2_scaling",mbpu);
    pipeline_result_t result;
    if(mode=="pipeline3_mesh")result=scheduler.pipeline3(mbpu,&intra_transfer_full_mesh);
    else if(mode=="pipeline3_spread")result=scheduler.pipeline3(mbpu,&spread_out);
    else result=scheduler.pipeline2(mbpu);
    std::cout<<std::setprecision(17)<<"{\"fast_us\":"<<result.t<<",\"non_inter_fraction\":"<<result.ratio
      <<",\"algbw_GB_s\":"<<total*mbpu*1000/(r*result.t)<<"}\n";
  }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}

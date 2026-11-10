// ============================================================================
// reproduction/driver.cc：论文复现实验的独立驱动。
//
// 目标：用“未修改的核心调度器”测量 FAST 与 Spread-out，并同时给出
// 模型时间、CPU 规划时间和理论下界，便于核对论文中的 speedup/带宽数字。
//
// 参数（11 个，位置敏感）：
//   n g seed profile MBpu outGbps inGbps alphaScale matrixIn|- matrixOut
//   matrixIn == "-" 时用 std::rand(seed) 生成；否则读文本矩阵。
//   matrixOut 用二进制 4 字节小端保存同一矩阵，供其它实验复用。
//
// 输出字段：
//   fast_us/spread_us/ideal_us      —— 模型时间
//   pipeline_cpu_us/full_cpu_us     —— 纯 pipeline2 与含输入/初始化的墙钟时间
//   *_gbps_bytes                    —— 按“总字节 / 全部 GPU / 时间”折算的有效带宽
//   all_*                           —— 把 CPU 规划时间也计入的分母，观察规划开销影响
// ============================================================================
#include "global.h"
#include "link_profile.h"
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <memory>
#include <stdexcept>

using Clock=std::chrono::steady_clock;
// 严格解析无符号整数与有限浮点数，拒绝空串/尾随字符/NaN。
static uint64_t integer(const std::string& s){if(s.empty()||s.find_first_not_of("0123456789")!=std::string::npos)throw std::invalid_argument("unsigned integer required");return std::stoull(s);}
static double number(const std::string& s){size_t used=0;double v=std::stod(s,&used);if(used!=s.size()||!std::isfinite(v))throw std::invalid_argument("finite number required");return v;}
int main(int argc,char** argv) {
  try {
    if(argc!=11)throw std::invalid_argument("usage: native_paper n g seed profile MBpu outGbps inGbps alphaScale matrixIn|- matrixOut");
    auto nn=integer(argv[1]),gg=integer(argv[2]),seed=integer(argv[3]);
    if(nn<2||nn>64||gg<1||gg>16||seed>UINT32_MAX)throw std::invalid_argument("dimensions/seed out of range");
    unsigned n=nn,g=gg,r=n*g;std::string mode=argv[4];double mbpu=number(argv[5]);
    if(mbpu<=0||mbpu>1)throw std::invalid_argument("MBpu must be in (0,1]");
    // 先配置 profile，再取链路参数，保证后续 GlobalScheduler 使用同一组值。
    configure_link_profile(mode,number(argv[6]),number(argv[7]),number(argv[8]));
    // upstream-default 复现上游默认的 100G 设定；其余用 400G 枚举。
    auto inter_type=mode=="upstream-default"?ETHER100:ETHER400;
    auto inter=get_inter_link_info(inter_type),intra=get_intra_link_info(H100);
    std::vector<unsigned> data(r*r);std::string input=argv[9];
    // "-" 生成随机矩阵（无自环）；否则从文本文件读取。
    if(input=="-") {
      std::srand(seed);
      for(unsigned s=0;s<r;++s)for(unsigned d=0;d<r;++d)data[s*r+d]=s==d?0:std::rand()%10000;
    } else {
      std::ifstream stream(input);std::string token;
      for(auto& v:data){if(!(stream>>token))throw std::runtime_error("truncated matrix");auto value=integer(token);if(value>INT32_MAX)throw std::overflow_error("native signed cell limit");v=value;}
      if(stream>>token)throw std::runtime_error("trailing matrix entries");
    }
    // 统计总量、最大单元格，以及每个 server 的最大跨 server 发送/接收量。
    // cross_max 是理想下界的分子：单个 server 至少要串行搬完这么多。
    uint64_t total=0,cross_max=0;unsigned max_cell=0;
    for(unsigned s=0;s<r;++s){if(data[s*r+s])throw std::invalid_argument("nonzero diagonal");for(unsigned d=0;d<r;++d){total+=data[s*r+d];max_cell=std::max(max_cell,data[s*r+d]);}}
    for(unsigned s=0;s<n;++s){uint64_t sent=0,recv=0,row=0,col=0;
      for(unsigned i=0;i<g;++i)for(unsigned d=0;d<r;++d){row+=data[(s*g+i)*r+d];col+=data[d*r+s*g+i];if(d/g!=s){sent+=data[(s*g+i)*r+d];recv+=data[d*r+s*g+i];}}
      if(row>UINT32_MAX-n*g||col>UINT32_MAX-n*g)throw std::overflow_error("native aggregate range");
      cross_max=std::max(cross_max,std::max(sent,recv));
    }
    // 原生 pipeline 不支持“完全没有跨 server 需求”的退化输入。
    if(!cross_max)throw std::invalid_argument("zero cross-server demand unsupported by pristine pipeline");
    // 以固定小端格式保存矩阵，便于其它工具按字节级复现同一输入。
    std::ofstream output(argv[10],std::ios::binary);
    for(unsigned value:data){unsigned char bytes[4]={static_cast<unsigned char>(value),static_cast<unsigned char>(value>>8),static_cast<unsigned char>(value>>16),static_cast<unsigned char>(value>>24)};output.write(reinterpret_cast<char*>(bytes),4);}
    output.close();if(!output)throw std::runtime_error("matrix output failed");
    // begin 到 end 覆盖对象构造 + pipeline2；pipeline_start 只覆盖 pipeline2。
    auto begin=Clock::now();std::vector<std::unique_ptr<LocalScheduler>> owners;std::vector<LocalScheduler*> locals;
    for(unsigned s=0;s<n;++s){owners.emplace_back(new LocalScheduler(data.data()+s*g*r,g,n,s,H100));locals.push_back(owners.back().get());}
    GlobalScheduler scheduler(n,g,locals,inter_type,H100,false,mbpu);
    auto pipeline_start=Clock::now();auto result=scheduler.pipeline2(mbpu);auto end=Clock::now();
    double pipeline_us=std::chrono::duration<double,std::micro>(end-pipeline_start).count();
    double full_us=std::chrono::duration<double,std::micro>(end-begin).count();
    double spread=spread_out_baseline(data.data(),n,g,inter,intra,mbpu);
    // 理想下界：把每个 server 的跨机总量平摊到 g 块 GPU，再按单 GPU 链路的
    // inter.beta 串行传输；忽略竞争与固定延迟，因此只能作为乐观参考。
    double ideal=cross_max*mbpu*inter.beta/g;
    // 有效带宽：总 MB * 1000（MB→GB 的近似）/ (GPU 数 * us)。字段名保留
    // “_gbps_bytes”以表明它按 GB/s 折算，而不是 bit/s。
    auto algbw=[&](double time){return total*mbpu*1000./(r*time);};
    std::cout<<std::setprecision(17)<<"{\"profile\":\""<<mode<<"\",\"servers\":"<<n<<",\"gpus\":"<<g<<",\"seed\":"<<seed
      <<",\"mbpu\":"<<mbpu<<",\"inter_alpha\":"<<inter.alpha<<",\"inter_beta\":"<<inter.beta<<",\"intra_alpha\":"<<intra.alpha<<",\"intra_beta\":"<<intra.beta
      <<",\"total_units\":"<<total<<",\"max_cross_units\":"<<cross_max<<",\"max_cell\":"<<max_cell<<",\"fast_us\":"<<result.t<<",\"spread_us\":"<<spread
      <<",\"ideal_us\":"<<ideal<<",\"pipeline_cpu_us\":"<<pipeline_us<<",\"full_cpu_us\":"<<full_us
      <<",\"fast_gbps_bytes\":"<<algbw(result.t)<<",\"spread_gbps_bytes\":"<<algbw(spread)<<",\"ideal_gbps_bytes\":"<<algbw(ideal)
      <<",\"all_gbps_bytes\":"<<algbw(result.t+pipeline_us)<<",\"all_full_gbps_bytes\":"<<algbw(result.t+full_us)<<"}\n";
  }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}

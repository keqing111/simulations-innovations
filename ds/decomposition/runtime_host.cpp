// Thin CPU-only adapter. All volume planning below calls unchanged NVIDIA sources.
// No CUDA kernels, transport, chunks, or real network timing are simulated here.
#include "fast_alltoall/alltoall_global_scheduler.h"
#include <algorithm>
#include <chrono>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <vector>

struct Stage { uint64_t q; std::vector<uint> p; };
using Values = std::vector<uint64_t>;
struct Quiet {
    std::ostringstream sink;
    std::streambuf* old;
    Quiet(): old(std::cout.rdbuf(sink.rdbuf())) {}
    ~Quiet() { std::cout.rdbuf(old); }
};

// The upstream short initializer allocates CPU objects only. Its general free
// routine assumes the *long* CUDA initializer, so release the CPU objects here.
struct Host {
    GlobalScheduler gs{};
    Host(uint n, uint g, Values& data) { init_global_scheduler(&gs,n,g,data.data(),0); }
    ~Host() {
        for(uint s=0;s<gs.server_n;++s) { free_local_scheduler(gs.locals[s]); free(gs.locals[s]); }
        free_matrix(&gs.mat); free(gs.temp_data); free(gs.sched);
        free(gs.gpu_sched); free(gs.buff_parameter);
    }
};

template<class T> void array_json(const std::vector<T>& a) {
    std::cout<<'['; for(size_t i=0;i<a.size();++i) { if(i)std::cout<<','; std::cout<<a[i]; } std::cout<<']';
}
void nested_json(const std::vector<Values>& a) {
    std::cout<<'['; for(size_t i=0;i<a.size();++i) { if(i)std::cout<<','; array_json(a[i]); } std::cout<<']';
}
Values matrix_values(Matrix& m) {
    Values r; for(uint i=0;i<m.dim;++i)for(uint j=0;j<m.dim;++j)r.push_back(get_matrix(&m,i,j)); return r;
}

std::vector<Stage> extract(GlobalScheduler& gs, Values& padded) {
    FastAll2All a{}; init_fastall2all(&a,&gs.mat);
    to_scaled_doubly_stochastic_matrix_fastall2all(&a);
    padded=matrix_values(a.SDS_mat);
    decompose_fastall2all(&a);
    std::vector<Stage> result;
    for(int k=int(a.p_sets_n)-1;k>=0;--k) {
        auto& ps=a.p_sets[a.p_sets_ascending[k]];
        Stage st{get_freq_permutation_set(&ps),std::vector<uint>(gs.server_n)};
        to_server_permutation_set(&ps,gs.server_n,st.p.data()); result.push_back(st);
    }
    free_fastall2all(&a); return result;
}

void validate(const std::vector<Stage>& plan, Values residual,uint n) {
    if(plan.size()>=MAX_TRANSFER_STEP_NUM)throw std::runtime_error("stage buffer exceeded");
    for(auto& st:plan) {
        if(!st.q||st.p.size()!=n)throw std::runtime_error("bad stage");
        std::vector<bool> used(n,false);
        for(uint s=0;s<n;++s) {
            uint d=st.p[s];
            if(d>=n||used[d])throw std::runtime_error("not a permutation"); used[d]=true;
            // Execution may reorder a previously valid decomposition: q need
            // not equal the current residual minimum, but cannot exceed it.
            if(st.q>residual[s*n+d])throw std::runtime_error("coefficient exceeds residual capacity");
            residual[s*n+d]-=st.q;
        }
    }
    for(auto v:residual)if(v)throw std::runtime_error("incomplete padded reconstruction");
}

void compose(GlobalScheduler& gs,const std::vector<Stage>& plan) {
    for(uint s=0;s<gs.server_n;++s)for(uint d=0;d<gs.server_n;++d)
        if(s!=d)balance_one_server(gs.locals[s],d,&gs.sched->balance[s][d]);
    get_buffer_size(&gs);
    for(uint s=0;s<gs.server_n;++s)
        memcpy(gs.sched->intrinsic_ata[s],gs.locals[s]->intrinsic_all2all,gs.gpu_n*gs.gpu_n*sizeof(uint64_t));
    for(size_t k=0;k<plan.size();++k)for(uint s=0;s<gs.server_n;++s) {
        uint d=plan[k].p[s];
        restore_one_server(gs.locals[s],d,&gs.sched->steps[k].channel[s],
            &gs.sched->steps[k].crossnode_sz[s],&gs.sched->steps[k+1].restore[s],
            &gs.sched->steps[k+1].restore_alltoall_sz[s],&gs.sched->steps[k+1].direct_cpy[s],plan[k].q);
        gs.sched->steps[k].to_server[s]=d; gs.sched->steps[k].from_server[d]=s;
    }
    gs.sched->step_n=plan.size()+1; schedule_this_gpu(&gs);
}

int main(int argc,char** argv) {
    try {
        if(argc<3||argc>4)throw std::runtime_error("usage: runtime_host_reference n g [replay.txt]");
        uint n=std::stoul(argv[1]),g=std::stoul(argv[2]),r=n*g;
        if(!n||n>MAX_SERVER_NUM||!g||g>MAX_GPU_PER_SERVER)throw std::runtime_error("native buffer dimensions exceeded");
        Values data(r*r); std::string token;
        for(auto& v:data) {
            if(!(std::cin>>token)||token.empty()||token[0]=='-')throw std::runtime_error("invalid demand");
            size_t end=0; v=std::stoull(token,&end);
            if(end!=token.size()||v>1000000000ULL)throw std::runtime_error("demand exceeds safe experiment range");
        }
        if(std::cin>>token)throw std::runtime_error("trailing demand");
        // Upstream matrix values are uint64, but update_permutation_sets returns
        // uint. Conservatively bound the total before calling untouched code.
        uint64_t total=0;
        for(auto v:data) {
            if(v>std::numeric_limits<uint>::max()-total)
                throw std::runtime_error("demand outside safe native integer domain");
            total+=v;
        }
        for(uint i=0;i<r;++i)if(data[i*r+i])throw std::runtime_error("self-routes belong outside communication demand");
        auto host=std::unique_ptr<Host>(new Host(n,g,data));
        Values padded; auto native_plan=extract(host->gs,padded),plan=native_plan;
        bool native_equal=false; double cpu_us=0;
        {
        Quiet quiet;
        if(argc==4) {
            std::ifstream in(argv[3]); uint count;
            if(!(in>>count)||count>=MAX_TRANSFER_STEP_NUM)throw std::runtime_error("invalid replay length");
            plan.clear();
            for(uint k=0;k<count;++k) {
                Stage st{0,std::vector<uint>(n)};
                if(!(in>>st.q))throw std::runtime_error("missing replay coefficient");
                for(auto& d:st.p)if(!(in>>d))throw std::runtime_error("missing replay endpoint");
                plan.push_back(st);
            }
            if(in>>token)throw std::runtime_error("trailing replay");
            validate(plan,padded,n); compose(host->gs,plan);
        } else {
            validate(plan,padded,n);
            auto start=std::clock(); run_scheduler(&host->gs);
            cpu_us=double(std::clock()-start)*1e6/CLOCKS_PER_SEC;
            auto replay=std::unique_ptr<Host>(new Host(n,g,data)); compose(replay->gs,plan);
            native_equal=memcmp(host->gs.sched,replay->gs.sched,sizeof(scheduling_result_t))==0;
            if(!native_equal)throw std::runtime_error("original run_scheduler / replay plan mismatch");
        }
        }
        auto& gs=host->gs; auto& sched=*gs.sched;
        std::vector<Values> balance(n,Values(g*g)),intrinsic(n,Values(g*g)),payload;
        std::vector<std::vector<Values>> dispatch;
        Values delivered(r*r);
        for(uint s=0;s<n;++s)for(uint i=0;i<g;++i)for(uint j=0;j<g;++j) {
            intrinsic[s][i*g+j]=sched.intrinsic_ata[s][i*g+j];
            delivered[(s*g+i)*r+s*g+j]+=intrinsic[s][i*g+j];
            if(i!=j)for(uint d=0;d<n;++d)if(s!=d)for(uint f=0;f<g;++f)
                balance[s][i*g+j]+=sched.balance[s][d][i*g+j].sz[f];
        }
        for(size_t k=0;k<plan.size();++k) {
            Values pay(n*g); std::vector<Values> dis(n,Values(g*g));
            for(uint s=0;s<n;++s) {
                uint d=plan[k].p[s];
                if(sched.steps[k].to_server[s]!=d)throw std::runtime_error("native execution order mismatch");
                for(uint i=0;i<g;++i) {
                    pay[s*g+i]=sched.steps[k].crossnode_sz[s][i];
                    for(uint j=0;j<g;++j) {
                        if(i!=j)dis[s][i*g+j]=sched.steps[k+1].restore_alltoall_sz[s][i][j];
                        for(uint f=0;f<g;++f)delivered[(s*g+f)*r+d*g+j]+=sched.steps[k].channel[s][i][j*g+f];
                    }
                }
            }
            payload.push_back(pay); dispatch.push_back(dis);
        }
        if(delivered!=data)throw std::runtime_error("GPU source/destination provenance reconstruction failed");
        std::cout<<std::setprecision(17)<<"{\"n\":"<<n<<",\"g\":"<<g<<",\"mode\":\"pipeline\",\"stage_count\":"<<plan.size()
            <<",\"native_replay_equal\":"<<(native_equal?"true":"false")<<",\"native_cpu_us\":"<<cpu_us;
        std::cout<<",\"native_profile_ms\":"<<(argc==3?gs.opt.flash_total_time:0)<<",\"server_matrix\":"; array_json(matrix_values(gs.mat));
        std::cout<<",\"padded_matrix\":";array_json(padded);
        std::cout<<",\"delivered_matrix\":";array_json(delivered);
        std::cout<<",\"balance_matrices\":";nested_json(balance);
        std::cout<<",\"intrinsic_matrices\":";nested_json(intrinsic);
        std::cout<<",\"payload_units\":";nested_json(payload);
        std::cout<<",\"dispatch_matrices\":[";
        for(size_t k=0;k<dispatch.size();++k) { if(k)std::cout<<',';nested_json(dispatch[k]); }
        std::cout<<"],\"permutations\":[";
        for(size_t k=0;k<plan.size();++k) { if(k)std::cout<<',';array_json(plan[k].p); }
        std::cout<<"],\"steps\":[";
        for(size_t k=0;k<plan.size();++k) { if(k)std::cout<<',';std::cout<<"{\"alpha\":"<<plan[k].q<<",\"alpha_scaled\":"<<plan[k].q<<'}'; }
        std::cout<<"]}\n"; return 0;
    } catch(const std::exception& e) { std::cerr<<"host reference: "<<e.what()<<'\n'; return 1; }
}

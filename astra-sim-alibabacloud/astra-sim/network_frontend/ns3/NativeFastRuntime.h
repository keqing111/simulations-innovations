#ifndef SIMAI_NATIVE_FAST_RUNTIME_H
#define SIMAI_NATIVE_FAST_RUNTIME_H

#include <functional>
#include <cstdint>
#include <map>
#include <set>
#include <stdexcept>
#include <vector>

struct NativeFastCommand { int rank,stage,send,recv,peer; };
struct NativeFastLocalTimes {
  int gpus=0; // zero retains the historical ideal-scaleup model
  std::vector<int64_t> balance_ns,intrinsic_ns;
  std::vector<std::vector<int64_t>> dispatch_ns;
};

// Dependency-level model of nonchunked NVIDIA FAST. Signal visibility is a
// durable event for multiple readers, not a simulation of CUDA counter races.
class NativeFastRuntime {
 public:
  using SendCredit=std::function<void(int,int,int)>;
  using Trace=std::function<void(const char*,int,int,int)>;
  using Timer=std::function<void(int64_t,std::function<void()>)>;
  NativeFastRuntime(int ranks,int stages,const std::vector<NativeFastCommand>& commands,
                    SendCredit credit,Trace trace,bool global_send_barrier=false,
                    NativeFastLocalTimes local={},Timer timer={},bool round_protocol=false)
      : n_(ranks),k_(stages),commands_(ranks*stages),send_(ranks),redis_(ranks),
        grant_(ranks),credit_(credit),trace_(trace),global_send_barrier_(global_send_barrier),
        local_(local),timer_(timer),round_protocol_(round_protocol) {
    if(n_<=0||k_<0||commands.size()!=size_t(n_*k_))throw std::runtime_error("invalid native command dimensions");
    std::set<int> slots;
    for(auto c:commands){
      if(c.rank<0||c.rank>=n_||c.stage<0||c.stage>=k_||c.peer<0||c.peer>=n_||
         !slots.insert(c.stage*n_+c.rank).second)throw std::runtime_error("invalid native command");
      commands_[c.stage*n_+c.rank]=c;
      if(c.send>=0&&!out_.emplace(c.send,c).second)throw std::runtime_error("duplicate native sender");
      if(c.recv>=0&&!in_.emplace(c.recv,c).second)throw std::runtime_error("duplicate native receiver");
    }
    if(out_.size()!=in_.size())throw std::runtime_error("native send/receive count mismatch");
    for(auto item:out_)if(!in_.count(item.first)||in_.at(item.first).stage!=item.second.stage)
      throw std::runtime_error("native send/receive stage mismatch");
    if(local_.gpus){
      int servers=n_/local_.gpus;
      if(local_.gpus<1||n_%local_.gpus||!timer_||local_.balance_ns.size()!=size_t(servers)||
         local_.intrinsic_ns.size()!=size_t(servers)||local_.dispatch_ns.size()!=size_t(k_))
        throw std::runtime_error("invalid native local timing dimensions");
      auto valid=[servers](const std::vector<int64_t>& v){
        if(v.size()!=size_t(servers))throw std::runtime_error("invalid native local stage dimensions");
        for(auto t:v)if(t<0)throw std::runtime_error("negative native local time");
      };
      valid(local_.balance_ns);valid(local_.intrinsic_ns);
      for(auto row:local_.dispatch_ns)valid(row);
      balanced_.resize(servers);intrinsic_.resize(servers);busy_.resize(servers);local_stage_.resize(servers);
    }
  }
  void start(){if(!begun_){
    begun_=true;trace_("begin",-1,-1,-1);
    for(size_t s=0;s<balanced_.size();++s){
      trace_("balance_begin",-1,s*local_.gpus,-1);
      timer_(local_.balance_ns[s],[this,s](){
        balanced_[s]=true;trace_("balance_done",-1,s*local_.gpus,-1);
        trace_("intrinsic_begin",-1,s*local_.gpus,-1);
        timer_(local_.intrinsic_ns[s],[this,s](){
          intrinsic_[s]=true;trace_("intrinsic_done",-1,s*local_.gpus,-1);pump();
        });pump();
      });
    }
    pump();
  }}
  void enqueue(int id,std::function<void()> release){
    if(!out_.count(id)||!callbacks_.emplace(id,std::move(release)).second)
      throw std::runtime_error("unknown or duplicate native payload registration");
    pump();
  }
  void credit_arrived(int id){
    if(!issued_.count(id)||!credits_.insert(id).second)throw std::runtime_error("unexpected native credit");
    const auto c=out_.at(id);trace_("credit_arrive",id,c.rank,c.stage);pump();
  }
  void received(int id){
    if(!started_.count(id)||!visible_.insert(id).second)throw std::runtime_error("unexpected native visibility");
    auto c=in_.at(id);trace_("data_ready",id,c.rank,c.stage);pump();
  }
  void acknowledged(int id){
    if(!visible_.count(id)||!acks_.insert(id).second)throw std::runtime_error("unexpected native ACK");
    auto c=out_.at(id);trace_("ack_done",id,c.rank,c.stage);pump();
  }
  bool finished()const{
    if(!begun_)return k_==0;
    for(size_t s=0;s<balanced_.size();++s)if(!balanced_[s]||!intrinsic_[s])return false;
    for(int r=0;r<n_;++r)if(send_[r]!=k_||redis_[r]!=k_||grant_[r]!=k_)return false;
    return true;
  }
 private:
  const NativeFastCommand& at(int rank,int stage)const{return commands_[stage*n_+rank];}
  bool visible(int rank,int stage)const{
    return stage<0||at(rank,stage).recv<0||visible_.count(at(rank,stage).recv);
  }
  void pump(){
    if(!begun_||pumping_)return;
    pumping_=true;
    bool progress=true;
    while(progress){
      progress=false;
      if(local_.gpus)for(size_t s=0;s<balanced_.size();++s){
        int stage=local_stage_[s];bool ready=intrinsic_[s]&&!busy_[s]&&stage<k_;
        if(ready)for(int u=0;u<local_.gpus;++u)ready=ready&&visible(s*local_.gpus+u,stage);
        if(ready){
          busy_[s]=true;trace_("redis_begin",-1,s*local_.gpus,stage);
          timer_(local_.dispatch_ns[stage][s],[this,s,stage](){
            for(int u=0;u<local_.gpus;++u){int rank=s*local_.gpus+u;
              trace_("redis_done",at(rank,stage).recv,rank,stage);++redis_[rank];}
            ++local_stage_[s];busy_[s]=false;pump();
          });
        }
      }
      for(int r=0;r<n_;++r){
        if(local_.gpus&&!balanced_[r/local_.gpus])continue;
        while(!local_.gpus&&redis_[r]<k_&&visible(r,redis_[r])){
          trace_("redis_done",at(r,redis_[r]).recv,r,redis_[r]);++redis_[r];progress=true;
        }
        while(grant_[r]<k_){
          int stage=grant_[r];auto c=at(r,stage);
          if(round_protocol_&&redis_[r]<stage)break;
          // Source stream3 skips empty incoming transfers. Its grant kernel
          // waits prior receive and, from stage2, release of reused buffer.
          if(c.recv>=0&&stage>0&&(!visible(r,stage-1)||redis_[r]<stage-1))break;
          ++grant_[r];progress=true;
          if(c.recv>=0){issued_.insert(c.recv);trace_("credit_send",c.recv,r,stage);
            credit_(c.recv,r,out_.at(c.recv).rank);}
          else if(stage==0){trace_("unused_credit_send",-1-r,r,stage);credit_(-1-r,r,c.peer);}
        }
        while(send_[r]<k_){
          auto c=at(r,send_[r]);
          if(c.send<0||acks_.count(c.send)){++send_[r];progress=true;continue;}
          bool barrier_clear=true;
          if(global_send_barrier_)for(const auto& previous:out_)
            if(previous.second.stage<c.stage&&!acks_.count(previous.first)){barrier_clear=false;break;}
          if(barrier_clear&&(!round_protocol_||redis_[r]>=c.stage)&&
             !started_.count(c.send)&&credits_.count(c.send)&&callbacks_.count(c.send)){
            started_.insert(c.send);trace_("data_release",c.send,r,c.stage);
            auto callback=callbacks_.at(c.send);callback();progress=true;
          }
          break;
        }
      }
    }
    if(finished()&&!finished_logged_){finished_logged_=true;trace_("finished",-1,-1,-1);}
    pumping_=false;
  }
  int n_,k_;
  std::vector<NativeFastCommand> commands_;
  std::vector<int> send_,redis_,grant_;
  std::map<int,NativeFastCommand> out_,in_;
  std::map<int,std::function<void()>> callbacks_;
  std::set<int> issued_,credits_,started_,visible_,acks_;
  SendCredit credit_;Trace trace_;
  bool begun_=false,pumping_=false,finished_logged_=false;
  bool global_send_barrier_=false;
  NativeFastLocalTimes local_;Timer timer_;
  std::vector<bool> balanced_,intrinsic_,busy_;
  std::vector<int> local_stage_;
  bool round_protocol_=false;
};
#endif

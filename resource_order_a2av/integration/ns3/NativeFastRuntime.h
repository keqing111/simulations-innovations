#ifndef RESOURCE_ORDER_WINDOW_RUNTIME_H
#define RESOURCE_ORDER_WINDOW_RUNTIME_H

// Build-only wrapper: the original runtime remains byte-for-byte unchanged.
// C=1 delegates every operation to it. C>1 uses FIFO, pooled endpoint slots;
// receive reservations last from credit issue to data arrival, send slots from
// data release to ACK. No extra bandwidth, bypass, barrier, or message splitting.
#define NativeFastRuntime SingleSlotNativeFastRuntime
#include "HistoricalNativeFastRuntime.h"
#undef NativeFastRuntime
#include <cstdlib>
#include <memory>
#include <string>

class NativeFastRuntime {
 public:
  using SendCredit=SingleSlotNativeFastRuntime::SendCredit;
  using Trace=SingleSlotNativeFastRuntime::Trace;
  using Timer=SingleSlotNativeFastRuntime::Timer;
  NativeFastRuntime(int ranks,int stages,const std::vector<NativeFastCommand>& commands,
                    SendCredit credit,Trace trace,bool barrier=false,
                    NativeFastLocalTimes local={},Timer timer={},bool rounds=false)
      : n_(ranks),credit_(credit),trace_(trace) {
    const char* env=std::getenv("SIMAI_RESOURCE_ORDER_CONCURRENCY");
    if(env){
      std::string value(env);size_t used=0;
      capacity_=std::stoi(value,&used);
      if(used!=value.size()||capacity_<1||capacity_>1024)
        throw std::runtime_error("invalid endpoint concurrency");
    }
    // Validate legacy ABI even in multi-slot mode.
    legacy_.reset(new SingleSlotNativeFastRuntime(ranks,stages,commands,credit,trace,
                                                  barrier,local,timer,rounds));
    if(capacity_==1)return;
    if(barrier||rounds||local.gpus)
      throw std::runtime_error("multi-slot mode is only for barrier-free Resource-Order");
    sendq_.resize(n_);recvq_.resize(n_);sp_.resize(n_);rp_.resize(n_);
    active_send_.resize(n_);active_recv_.resize(n_);
    std::vector<NativeFastCommand> ordered(ranks*stages);
    for(auto c:commands)ordered[c.stage*ranks+c.rank]=c;
    for(auto c:ordered){
      if(c.send>=0){sendq_[c.rank].push_back(c.send);out_[c.send]=c;}
      if(c.recv>=0){recvq_[c.rank].push_back(c.recv);in_[c.recv]=c;}
    }
  }
  void start(){
    if(capacity_==1){legacy_->start();return;}
    if(!begun_){begun_=true;trace_("begin",-1,-1,-1);pump();}
  }
  void enqueue(int id,std::function<void()> release){
    if(capacity_==1){legacy_->enqueue(id,release);return;}
    if(!out_.count(id)||!callbacks_.emplace(id,release).second)
      throw std::runtime_error("unknown/duplicate multi-slot payload");
    pump();
  }
  void credit_arrived(int id){
    if(capacity_==1){legacy_->credit_arrived(id);return;}
    if(!issued_.count(id)||!credits_.insert(id).second)
      throw std::runtime_error("unexpected multi-slot credit");
    auto c=out_.at(id);trace_("credit_arrive",id,c.rank,c.stage);pump();
  }
  void received(int id){
    if(capacity_==1){legacy_->received(id);return;}
    if(!started_.count(id)||!visible_.insert(id).second)
      throw std::runtime_error("unexpected multi-slot data");
    auto c=in_.at(id);
    if(--active_recv_[c.rank]<0)throw std::runtime_error("receive slot underflow");
    trace_("data_ready",id,c.rank,c.stage);pump();
  }
  void acknowledged(int id){
    if(capacity_==1){legacy_->acknowledged(id);return;}
    if(!visible_.count(id)||!acks_.insert(id).second)
      throw std::runtime_error("unexpected multi-slot ACK");
    auto c=out_.at(id);
    if(--active_send_[c.rank]<0)throw std::runtime_error("send slot underflow");
    trace_("ack_done",id,c.rank,c.stage);pump();
  }
  bool finished()const{
    if(capacity_==1)return legacy_->finished();
    return begun_&&acks_.size()==out_.size();
  }
 private:
  void pump(){
    if(!begun_||pumping_)return;
    pumping_=true;
    bool progress=true;
    while(progress){
      progress=false;
      for(int rank=0;rank<n_;++rank){
        while(rp_[rank]<recvq_[rank].size()&&active_recv_[rank]<capacity_){
          int id=recvq_[rank][rp_[rank]++];auto c=in_.at(id);
          ++active_recv_[rank];issued_.insert(id);progress=true;
          trace_("credit_send",id,rank,c.stage);credit_(id,rank,out_.at(id).rank);
        }
        while(sp_[rank]<sendq_[rank].size()&&active_send_[rank]<capacity_){
          int id=sendq_[rank][sp_[rank]];
          // FIFO start order: later ready messages cannot bypass an unready head.
          if(!credits_.count(id)||!callbacks_.count(id))break;
          ++sp_[rank];++active_send_[rank];started_.insert(id);progress=true;
          trace_("data_release",id,rank,out_.at(id).stage);callbacks_.at(id)();
        }
      }
    }
    if(finished()&&!finished_logged_){finished_logged_=true;trace_("finished",-1,-1,-1);}
    pumping_=false;
  }
  int n_,capacity_=1;
  SendCredit credit_;Trace trace_;
  std::unique_ptr<SingleSlotNativeFastRuntime> legacy_;
  std::vector<std::vector<int>> sendq_,recvq_;
  std::vector<size_t> sp_,rp_;
  std::vector<int> active_send_,active_recv_;
  std::map<int,NativeFastCommand> out_,in_;
  std::map<int,std::function<void()>> callbacks_;
  std::set<int> issued_,credits_,started_,visible_,acks_;
  bool begun_=false,pumping_=false,finished_logged_=false;
};
#endif

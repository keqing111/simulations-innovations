#include "NativeFastRuntime.h"
#include <cassert>
#include <iostream>
#include <queue>
#include <tuple>

// Deterministic discrete-event contract test, not a performance experiment.
int main(){
  for(int slots:{1,2,4,8}){
    setenv("SIMAI_RESOURCE_ORDER_CONCURRENCY",std::to_string(slots).c_str(),1);
    std::vector<NativeFastCommand> commands;
    const int n=12,k=n-1;
    for(int stage=0;stage<k;++stage)for(int r=0;r<n;++r)
      commands.push_back({r,stage,stage*n+r,stage*n+(r-stage-1+n)%n,r});
    using Event=std::tuple<int,int,std::function<void()>>;
    auto cmp=[](const Event&a,const Event&b){return std::get<0>(a)!=std::get<0>(b)?
      std::get<0>(a)>std::get<0>(b):std::get<1>(a)>std::get<1>(b);};
    std::priority_queue<Event,std::vector<Event>,decltype(cmp)> queue(cmp);
    int now=0,seq=0,finishes=0,max_send=0,max_recv=0;
    std::vector<int> as(n),ar(n),send_count(n),recv_count(n);
    std::map<int,int> release,ready,ack;
    NativeFastRuntime* ptr=nullptr;
    auto schedule=[&](int dt,std::function<void()> fn){queue.emplace(now+dt,seq++,fn);};
    NativeFastRuntime runtime(n,k,commands,
      [&](int id,int,int){schedule(2,[&,id](){ptr->credit_arrived(id);});},
      [&](const char* event,int id,int rank,int stage){
        std::string e(event);
        if(e=="credit_send"){
          assert(stage==recv_count[rank]++);assert(++ar[rank]<=slots);
          max_recv=std::max(max_recv,ar[rank]);
        }
        if(e=="data_release"){
          assert(stage==send_count[rank]++);assert(++as[rank]<=slots);
          max_send=std::max(max_send,as[rank]);release[id]=now;
          if(slots==1&&stage)assert(ack.count(id-n)&&ack[id-n]<=now);
        }
        if(e=="data_ready"){assert(--ar[rank]>=0);ready[id]=now;}
        if(e=="ack_done"){assert(--as[rank]>=0);assert(ready.count(id));ack[id]=now;}
        if(e=="finished")++finishes;
      });ptr=&runtime;
    for(int id=0;id<n*k;++id)runtime.enqueue(id,[&,id](){
      // Later messages can finish sooner: tests pooled-slot reuse.
      int duration=id/n==0?100:3;
      schedule(duration,[&,id](){runtime.received(id);});
      schedule(duration+5,[&,id](){runtime.acknowledged(id);});
    });
    runtime.start();int steps=0;
    while(!queue.empty()){
      auto item=queue.top();queue.pop();now=std::get<0>(item);std::get<2>(item)();
      assert(++steps<10000);
    }
    assert(runtime.finished()&&finishes==1&&ack.size()==size_t(n*k));
    assert(max_send==slots&&max_recv==slots);
    if(slots>1)assert(release[2*n]<ack[0]); // slot of short second message reused
    for(int r=0;r<n;++r)assert(as[r]==0&&ar[r]==0&&send_count[r]==k&&recv_count[r]==k);
    std::cout<<"slots="<<slots<<" contract passed\n";
  }
  setenv("SIMAI_RESOURCE_ORDER_CONCURRENCY","0",1);
  bool rejected=false;
  try{NativeFastRuntime bad(1,0,{},[](int,int,int){},[](const char*,int,int,int){});}
  catch(const std::exception&){rejected=true;}
  assert(rejected);
}

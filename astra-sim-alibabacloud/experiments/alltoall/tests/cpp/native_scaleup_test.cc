#include "NativeFastRuntime.h"
#include <cassert>
#include <map>
#include <string>
#include <tuple>

int main(){
  using Key=std::pair<long long,int>;
  long long now=0;int serial=0;
  std::map<Key,std::function<void()>> pending;
  std::map<std::tuple<std::string,int,int>,long long> seen;
  auto timer=[&](int64_t delay,std::function<void()> fn){pending[{now+delay,serial++}]=fn;};
  auto trace=[&](const char* kind,int,int rank,int stage){seen[{kind,rank,stage}]=now;};
  NativeFastLocalTimes local;local.gpus=2;
  local.balance_ns={10,100};local.intrinsic_ns={20,200};local.dispatch_ns={{30,40}};
  std::vector<NativeFastCommand> commands;
  for(int r=0;r<4;++r)commands.push_back({r,0,-1,-1,r});
  NativeFastRuntime runtime(4,1,commands,[](int,int,int){},trace,false,local,timer);
  runtime.start();assert(!runtime.finished());
  while(!pending.empty()){
    auto event=*pending.begin();pending.erase(pending.begin());now=event.first.first;event.second();
  }
  assert(runtime.finished());
  assert((seen[{"balance_done",0,-1}]==10));
  assert((seen[{"intrinsic_done",0,-1}]==30));
  assert((seen[{"redis_done",0,0}]==60));
  assert((seen[{"redis_done",1,0}]==60));
  assert((seen[{"redis_done",2,0}]==340));
  assert((seen[{"finished",-1,-1}]==340));
  // The first server finishes locally before another server has balanced:
  // local dependencies, not a global stage or balance barrier.
  assert((seen[{"redis_done",0,0}]<seen[{"balance_done",2,-1}]));

  // Even a local-only operation with zero network stages pays its head/tail.
  pending.clear();seen.clear();now=0;local.dispatch_ns.clear();
  NativeFastRuntime empty(4,0,{},[](int,int,int){},trace,false,local,timer);
  empty.start();assert(!empty.finished());
  while(!pending.empty()){
    auto event=*pending.begin();pending.erase(pending.begin());now=event.first.first;event.second();
  }
  assert(empty.finished());assert((seen[{"finished",-1,-1}]==300));

  pending.clear();seen.clear();now=0;local.gpus=1;
  local.balance_ns={0,0};local.intrinsic_ns={0,0};local.dispatch_ns={{100,200},{0,0}};
  NativeFastRuntime* current=nullptr;
  auto credit=[&](int id,int,int){if(id>=0)timer(0,[&,id](){current->credit_arrived(id);});};
  NativeFastRuntime rounds(2,2,{{0,0,-1,-1,1},{1,0,-1,-1,0},
                               {0,1,0,1,1},{1,1,1,0,0}},credit,trace,false,local,timer,true);
  current=&rounds;
  for(int id=0;id<2;++id)rounds.enqueue(id,[&,id](){
    rounds.received(id);rounds.acknowledged(id);
  });
  rounds.start();
  while(!pending.empty()){
    auto event=*pending.begin();pending.erase(pending.begin());now=event.first.first;event.second();
  }
  assert(rounds.finished());
  assert((seen[{"data_release",0,1}]>=200));
  assert((seen[{"data_release",1,1}]>=200));
}

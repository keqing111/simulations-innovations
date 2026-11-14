#include "NativeFastRuntime.h"
#include <cassert>
#include <set>

int main(){
  std::vector<NativeFastCommand> commands;
  for(int k=0;k<2;++k)for(int r=0;r<4;++r)
    commands.push_back({r,k,k*4+r,k*4+(r^1),r^1});
  std::set<int> sent,credits;
  NativeFastRuntime runtime(4,2,commands,
    [&](int id,int,int){credits.insert(id);},[](const char*,int,int,int){});
  for(int id=0;id<8;++id)runtime.enqueue(id,[&,id]{sent.insert(id);});
  runtime.start();
  assert(credits==std::set<int>({0,1,2,3}));
  assert(sent.empty()); // credits must actually arrive, not appear magically.
  runtime.credit_arrived(0);runtime.credit_arrived(1);
  assert(sent==std::set<int>({0,1}));
  runtime.received(0);runtime.received(1);
  assert(credits.count(4)&&credits.count(5));
  runtime.credit_arrived(4);runtime.credit_arrived(5);
  assert(!sent.count(4)); // local send stream still awaits ACK/quiet.
  runtime.acknowledged(0);runtime.acknowledged(1);
  assert(sent.count(4)&&sent.count(5));
  assert(!sent.count(2)&&!sent.count(3)); // unrelated slow component not a barrier.
  runtime.received(4);runtime.received(5);
  runtime.acknowledged(4);runtime.acknowledged(5);
  for(int id:{2,3,6,7}){
    runtime.credit_arrived(id);runtime.received(id);runtime.acknowledged(id);
  }
  assert(runtime.finished());
  bool rejected=false;
  try{runtime.received(0);}catch(const std::runtime_error&){rejected=true;}
  assert(rejected);
  std::set<int> buffer_credits;
  NativeFastRuntime buffers(2,3,{{0,0,0,1,1},{1,0,1,0,0},
       {0,1,-1,-1,1},{1,1,-1,-1,0},{0,2,2,3,1},{1,2,3,2,0}},
       [&](int id,int,int){buffer_credits.insert(id);},[](const char*,int,int,int){});
  for(int id=0;id<4;++id)buffers.enqueue(id,[]{});
  buffers.start();buffers.credit_arrived(0);buffers.credit_arrived(1);
  assert(!buffer_credits.count(2)&&!buffer_credits.count(3));
  buffers.received(0);
  assert(buffer_credits.count(2)&&!buffer_credits.count(3));
  buffers.received(1);buffers.acknowledged(0);buffers.acknowledged(1);
  buffers.credit_arrived(2);buffers.credit_arrived(3);
  buffers.received(2);buffers.received(3);buffers.acknowledged(2);buffers.acknowledged(3);
  assert(buffers.finished());
  NativeFastRuntime empty(2,2,{{0,0,-1,-1,1},{1,0,-1,-1,0},
       {0,1,-1,-1,1},{1,1,-1,-1,0}},[](int,int,int){},[](const char*,int,int,int){});
  empty.start();assert(empty.finished());
  std::set<int> fenced_sent;
  NativeFastRuntime fenced(4,2,commands,[](int,int,int){},[](const char*,int,int,int){},true);
  for(int id=0;id<8;++id)fenced.enqueue(id,[&,id]{fenced_sent.insert(id);});
  fenced.start();
  for(int id:{0,1}){fenced.credit_arrived(id);fenced.received(id);fenced.acknowledged(id);}
  fenced.credit_arrived(4);fenced.credit_arrived(5);
  assert(!fenced_sent.count(4)&&!fenced_sent.count(5));
  for(int id:{2,3}){fenced.credit_arrived(id);fenced.received(id);fenced.acknowledged(id);}
  assert(fenced_sent.count(4)&&fenced_sent.count(5));
}

#ifndef SIMAI_NATIVE_FAST_TRANSPORT_H
#define SIMAI_NATIVE_FAST_TRANSPORT_H

#include "NativeFastRuntime.h"
#include <memory>
#include <sstream>
#include <tuple>

std::unique_ptr<NativeFastRuntime> native_fast_runtime;
bool native_finish_pending=false;
std::ofstream native_fast_events,native_wan_packets,native_injections,native_wan_queues;
struct NativeWireRecord {
  int id;uint64_t logical,wire;bool control,visible;uint64_t injected=0;
};
using NativeWireKey=std::tuple<int,int,uint16_t>;
std::map<NativeWireKey,NativeWireRecord> native_wire_records;
std::map<NativeWireKey,NativeWireRecord> native_background_records;
std::ofstream native_background_events;
bool native_start_scheduled=false;
uint64_t native_foreground_start(){
  const char* value=std::getenv("SIMAI_FOREGROUND_START_NS");return value?std::stoull(value):0;
}
void native_start_when_ready(){
  if(native_start_scheduled)return;native_start_scheduled=true;
  uint64_t now=Simulator::Now().GetNanoSeconds(),start=native_foreground_start();
  if(start>now)Simulator::Schedule(NanoSeconds(start-now),[](){native_fast_runtime->start();});
  else native_fast_runtime->start();
}

void native_event(const char* event,int id,int rank,int stage){
  native_fast_events<<Simulator::Now().GetNanoSeconds()<<','<<event<<','<<id<<','<<rank<<','<<stage<<std::endl;
  if(std::string(event)=="finished"&&native_finish_pending)
    Simulator::ScheduleNow([](){std::exit(0);});
}
void native_noop(void*){}

void native_background_event(const char* event,const NativeWireRecord& record,int src,int dst,uint64_t size){
  native_background_events<<Simulator::Now().GetNanoSeconds()<<','<<event<<','<<record.id<<','<<src<<','<<dst<<','<<size<<std::endl;
}
void native_background_send(int src,int dst,uint64_t bytes,uint64_t interval,uint64_t horizon){
  static int sequence=0;
  if(uint64_t(Simulator::Now().GetNanoSeconds())>=horizon)return;
  uint16_t port=portNumber[src][dst]++;
  if(port<10000)throw std::runtime_error("background source port wrapped");
  NativeWireRecord record{-1000000-sequence++,bytes,bytes,false,false};
  native_background_records.emplace(NativeWireKey(src,dst,port),record);
  native_background_event("release",record,src,dst,bytes);
  RdmaClientHelper helper(3,serverAddress[src],serverAddress[dst],port,100,bytes,
      has_win?pairBdp[n.Get(src)][n.Get(dst)]:0,pairRtt[src][dst],native_noop,nullptr,0,src,dst);
  helper.Install(n.Get(src)).Start(NanoSeconds(0));
  Simulator::Schedule(NanoSeconds(interval),native_background_send,src,dst,bytes,interval,horizon);
}
void native_start_background(){
  const char* path=std::getenv("SIMAI_BACKGROUND_FILE");if(!path||!*path)return;
  std::ifstream input(path);std::string row;
  while(std::getline(input,row)){
    std::replace(row.begin(),row.end(),',',' ');std::istringstream line(row);
    int src,dst;uint64_t bytes,interval,horizon;
    if(!(line>>src>>dst>>bytes>>interval>>horizon)||src==dst||!bytes||!interval||!horizon)
      throw std::runtime_error("invalid background task");
    Simulator::ScheduleNow([=](){native_background_send(src,dst,bytes,interval,horizon);});
  }
}

void native_send_credit(int id,int src,int dst){
  if(src==dst){if(id>=0)native_fast_runtime->credit_arrived(id);return;}
  native_event("credit_wire_send",id,src,-1);
  uint16_t port=portNumber[src][dst]++;
  native_wire_records.emplace(NativeWireKey(src,dst,port),NativeWireRecord{id,0,8,true,false});
  RdmaClientHelper helper(3,serverAddress[src],serverAddress[dst],port,100,8,
      has_win?(global_t==1?maxBdp:pairBdp[n.Get(src)][n.Get(dst)]):0,
      global_t==1?maxRtt:pairRtt[src][dst],native_noop,nullptr,0,src,dst);
  helper.Install(n.Get(src)).Start(NanoSeconds(0));
}

void native_rx_progress(uint32_t sip,uint32_t dip,uint16_t port,uint64_t received){
  if(!native_fast_runtime)return;
  int src=ip_to_node_id(Ipv4Address(sip)),dst=ip_to_node_id(Ipv4Address(dip));
  auto found=native_wire_records.find(NativeWireKey(src,dst,port));
  if(found==native_wire_records.end()){
    auto bg=native_background_records.find(NativeWireKey(src,dst,port));
    if(bg!=native_background_records.end()&&!bg->second.visible&&received>=bg->second.wire){
      bg->second.visible=true;native_background_event("receive",bg->second,src,dst,received);}
    return;
  }
  auto& wire=found->second;
  if(!wire.visible&&received>=wire.wire){
    wire.visible=true;
    if(wire.control){if(wire.id>=0)native_fast_runtime->credit_arrived(wire.id);}
    else native_fast_runtime->received(wire.id);
  }
}

void native_wan_tx(uint32_t node,uint32_t port,uint64_t bw,Ptr<const Packet> packet){
  CustomHeader header(CustomHeader::L2_Header|CustomHeader::L3_Header|CustomHeader::L4_Header);
  packet->PeekHeader(header);
  int id=-1,kind=2;uint64_t logical=0,payload=0;
  if(header.l3Prot==0x11){
    auto found=native_wire_records.find(NativeWireKey(
      ip_to_node_id(Ipv4Address(header.sip)),ip_to_node_id(Ipv4Address(header.dip)),header.udp.sport));
    if(found!=native_wire_records.end()){id=found->second.id;kind=found->second.control?1:0;logical=found->second.logical;}
    else {auto bg=native_background_records.find(NativeWireKey(
      ip_to_node_id(Ipv4Address(header.sip)),ip_to_node_id(Ipv4Address(header.dip)),header.udp.sport));
      if(bg!=native_background_records.end()){id=bg->second.id;kind=3;logical=bg->second.logical;}}
    if(kind==0||kind==3){
      uint64_t seq=header.udp.seq,bytes=packet->GetSize()-header.GetSerializedSize();
      payload=std::min(logical,seq+bytes)-std::min(logical,seq);
    }
  }
  uint64_t duration=DataRate(bw).CalculateBytesTxTime(packet->GetSize()).GetNanoSeconds();
  native_wan_packets<<Simulator::Now().GetNanoSeconds()<<','<<node<<','<<port<<','
    <<duration<<','<<packet->GetSize()<<','<<kind<<','<<id<<','<<payload<<std::endl;
}

void native_nic_tx_end(Ptr<const Packet> packet){
  CustomHeader header(CustomHeader::L2_Header|CustomHeader::L3_Header|CustomHeader::L4_Header);
  packet->PeekHeader(header);
  if(header.l3Prot!=0x11)return;
  auto found=native_wire_records.find(NativeWireKey(
    ip_to_node_id(Ipv4Address(header.sip)),ip_to_node_id(Ipv4Address(header.dip)),header.udp.sport));
  if(found==native_wire_records.end()||found->second.control)return;
  auto& wire=found->second;
  uint64_t progress=std::min(wire.logical,uint64_t(header.udp.seq)+packet->GetSize()-header.GetSerializedSize());
  if(progress>wire.injected){wire.injected=progress;
    native_injections<<Simulator::Now().GetNanoSeconds()<<','<<wire.id<<','<<progress<<std::endl;}
}

void native_wan_queue(Ptr<QbbNetDevice> dev,uint32_t node,uint32_t port,Ptr<const Packet>,uint32_t){
  // QbbEnqueue fires BEFORE insertion. Observe the post-event state, including
  // any immediate dequeue/drop, rather than inventing an enqueue delta.
  Simulator::ScheduleNow([dev,node,port](){
    native_wan_queues<<Simulator::Now().GetNanoSeconds()<<','<<node<<','<<port<<','
        <<dev->GetQueue()->GetNBytesTotal()<<std::endl;
  });
}

void native_initialize(const std::string& path){
  std::ifstream input(path);std::string line;
  if(!std::getline(input,line))throw std::runtime_error("missing native command header");
  std::replace(line.begin(),line.end(),',',' ');std::istringstream header(line);
  int ranks,stages;
  if(!(header>>ranks>>stages))throw std::runtime_error("invalid native command header");
  int barrier=0;if(header>>barrier){if(barrier!=0&&barrier!=1)throw std::runtime_error("invalid native barrier flag");}
  int rounds=0;if(header>>rounds){if(rounds!=0&&rounds!=1)throw std::runtime_error("invalid round protocol flag");}
  std::vector<NativeFastCommand> commands;
  while(std::getline(input,line)){
    std::replace(line.begin(),line.end(),',',' ');std::istringstream row(line);
    NativeFastCommand c;std::string extra;
    if(!(row>>c.rank>>c.stage>>c.send>>c.recv>>c.peer)||(row>>extra))
      throw std::runtime_error("invalid native command row");
    commands.push_back(c);
  }
  native_fast_events.open(path+".events.csv");
  const auto slash=path.find_last_of('/');std::string base=slash==std::string::npos?"":path.substr(0,slash+1);
  native_wan_packets.open(base+"native_wan.csv");native_injections.open(base+"native_injection.csv");
  native_wan_queues.open(base+"native_wan_queue.csv");
  native_background_events.open(base+"background_events.csv");
  native_background_events<<"time_ns,event,flow_id,src,dst,bytes"<<std::endl;
  if(!native_fast_events||!native_wan_packets||!native_injections||!native_wan_queues)throw std::runtime_error("native trace creation failed");
  native_fast_events<<"time_ns,event,flow_id,rank,stage"<<std::endl;
  native_wan_packets<<"start_ns,node_id,port_id,duration_ns,wire_bytes,kind,flow_id,payload_bytes"<<std::endl;
  native_injections<<"time_ns,flow_id,injected_bytes"<<std::endl;
  native_wan_queues<<"time_ns,node_id,port_id,queue_bytes"<<std::endl;
  NativeFastLocalTimes local;
  const char* local_path=std::getenv("SIMAI_NATIVE_LOCAL_TIMES");
  if(local_path&&*local_path){
    std::ifstream timing(local_path);int servers,local_stages;
    if(!(timing>>servers>>local.gpus>>local_stages)||local.gpus<1||servers<1||
       servers*local.gpus!=ranks||local_stages!=stages)
      throw std::runtime_error("invalid native local timing header");
    auto read=[&](std::vector<int64_t>& row){row.resize(servers);
      for(auto& value:row)if(!(timing>>value)||value<0)throw std::runtime_error("invalid native local duration");};
    read(local.balance_ns);read(local.intrinsic_ns);local.dispatch_ns.resize(stages);
    for(auto& row:local.dispatch_ns)read(row);
    std::string extra;if(timing>>extra)throw std::runtime_error("trailing native local duration");
  }
  native_fast_runtime.reset(new NativeFastRuntime(ranks,stages,commands,native_send_credit,native_event,barrier,
      local,[](int64_t ns,std::function<void()> fn){Simulator::Schedule(NanoSeconds(ns),fn);},rounds));
}

void native_attach_traces(){
  if(!native_fast_runtime)return;
  for(uint32_t i=0;i<n.GetN();++i)if(n.Get(i)->GetNodeType()==0){
    n.Get(i)->GetObject<RdmaDriver>()->m_rdma->m_rxProgress.ConnectWithoutContext(MakeCallback(&native_rx_progress));
    for(uint32_t d=0;d<n.Get(i)->GetNDevices();++d){
      auto dev=DynamicCast<QbbNetDevice>(n.Get(i)->GetDevice(d));
      if(dev)dev->TraceConnectWithoutContext("PhyTxEnd",MakeCallback(&native_nic_tx_end));
    }
  }
  const char* ports=std::getenv("SIMAI_NATIVE_WAN_PORTS");
  if(ports){std::stringstream entries(ports);std::string entry;
    while(std::getline(entries,entry,',')){
      auto colon=entry.find(':');if(colon==std::string::npos)throw std::runtime_error("invalid native WAN port");
      uint32_t node=std::stoul(entry.substr(0,colon)),port=std::stoul(entry.substr(colon+1));
      auto dev=DynamicCast<QbbNetDevice>(n.Get(node)->GetDevice(port));
      if(!dev)throw std::runtime_error("native WAN device absent");
      dev->TraceConnectWithoutContext("PhyTxBegin",MakeBoundCallback(&native_wan_tx,node,port,dev->GetDataRate().GetBitRate()));
      dev->TraceConnectWithoutContext("QbbEnqueue",MakeBoundCallback(&native_wan_queue,dev,node,port));
      dev->TraceConnectWithoutContext("QbbDequeue",MakeBoundCallback(&native_wan_queue,dev,node,port));
    }
  }
}
#endif

#include "trace-format.h"
#include "sim-setting.h"
#include <cstddef>
#include <cstdio>

int main(int argc,char** argv){
  if(argc!=2)return 2;
  FILE* file=std::fopen(argv[1],"wb");if(!file)return 3;
  SimSetting settings;settings.win=123;settings.port_speed[0][1]=100000000000ULL;settings.Serialize(file);
  auto data=[&](uint64_t time,uint16_t node,uint8_t event,uint32_t seq,uint32_t qlen){
    ns3::TraceFormat t{};t.time=time;t.node=node;t.intf=node==2?2:1;t.event=event;t.qlen=qlen;
    t.sip=0x0b000001;t.dip=0x0b000101;t.l3Prot=0x11;t.qidx=3;
    t.data.sport=10000;t.data.dport=100;t.data.seq=seq;
    uint32_t payload=seq==0?65536:4;t.data.payload=payload;t.size=payload+52;t.Serialize(file);
  };
  data(10,0,2,0,0);data(20,2,1,0,1000000);
  data(25,0,2,65536,0);data(30,2,1,65536,0);data(31,2,2,65536,0);data(35,1,0,65536,0);
  ns3::TraceFormat nack{};nack.time=40;nack.node=0;nack.event=0;nack.l3Prot=0xfd;
  nack.sip=0x0b000101;nack.dip=0x0b000001;nack.ack.sport=100;nack.ack.dport=10000;nack.ack.seq=0;nack.Serialize(file);
  data(45,0,2,0,0);data(46,2,1,0,0);data(47,2,2,0,0);data(50,1,0,0,0);
  data(55,0,2,65536,0);data(56,2,1,65536,0);data(57,2,2,65536,0);data(60,1,0,65536,0);
  auto ack=nack;ack.time=70;ack.l3Prot=0xfc;ack.ack.seq=65540;ack.Serialize(file);std::fclose(file);
  std::printf("{\"record_bytes\":%zu,\"seq_offset\":%zu,\"ack_seq_offset\":%zu}\n",
    sizeof(ns3::TraceFormat),offsetof(ns3::TraceFormat,data.seq),offsetof(ns3::TraceFormat,ack.seq));
}

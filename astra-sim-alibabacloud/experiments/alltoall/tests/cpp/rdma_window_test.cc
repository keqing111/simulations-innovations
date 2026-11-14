#include <ns3/rdma-queue-pair.h>
#include <ns3/ptr.h>
#include <cstdio>
#include <cstdint>
#include <limits>

int main() {
  auto qp=ns3::CreateObject<ns3::RdmaQueuePair>(3,ns3::Ipv4Address("11.0.0.1"),
                  ns3::Ipv4Address("11.0.1.1"),100,100);
  const uint64_t rate=3478260869570ULL;
  qp->SetWin(125209775);qp->SetVarWin(true);
  qp->m_max_rate=ns3::DataRate(rate);qp->m_rate=ns3::DataRate(rate);
  qp->hp.m_curRate=ns3::DataRate(rate);
  auto check=[&](uint64_t expected,const char* label) {
    auto normal=qp->GetWin(),hp=qp->HpGetCurWin();
    if(normal!=expected || hp!=expected) {
      std::fprintf(stderr,"%s: expected%llu normal%llu hp%llu\n",label,
          (unsigned long long)expected,(unsigned long long)normal,(unsigned long long)hp);
      return false;
    }
    return true;
  };
  if(!check(125209775,"WAN BDP at full rate"))return 1;
  qp->m_rate=ns3::DataRate(rate/2);qp->hp.m_curRate=ns3::DataRate(rate/2);
  if(!check(62604887,"WAN BDP at half rate"))return 1;
  qp->SetVarWin(false);
  if(!check(125209775,"fixed window"))return 1;
  qp->SetVarWin(true);qp->SetWin(1);qp->m_rate=ns3::DataRate(1);qp->hp.m_curRate=ns3::DataRate(1);
  if(!check(1,"minimum positive window"))return 1;
  qp->SetWin(0);
  if(!check(0,"unlimited window"))return 1;
  qp->SetWin(std::numeric_limits<uint32_t>::max());
  qp->m_max_rate=ns3::DataRate(std::numeric_limits<uint64_t>::max());
  qp->m_rate=qp->m_max_rate;qp->hp.m_curRate=qp->m_max_rate;
  if(!check(4294967295ULL,"full integer range"))return 1;
  return 0;
}

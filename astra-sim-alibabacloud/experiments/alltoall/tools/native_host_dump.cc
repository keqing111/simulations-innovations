#include "fast_alltoall/alltoall_algorithm.h"
#include "fast_alltoall/alltoall_local_scheduler.h"
#include <algorithm>
#include <fstream>
#include <queue>
#include <stdexcept>
#include <vector>

static void flow(int src,int dst,uint64_t bytes,const char* phase,int stage) {
  if(bytes && src!=dst) std::cout<<"F "<<src<<' '<<dst<<' '<<bytes<<' '<<phase<<' '<<stage<<'\n';
}

int main(int argc,char** argv) {
  if(argc<5) throw std::runtime_error("native_host_dump servers gpus variant matrix [dcids...] [lan_bw wan_bw]");
  int n=std::stoi(argv[1]),g=std::stoi(argv[2]);
  std::string variant=argv[3];
  if(n<1||n>MAX_SERVER_NUM||g<1||g>MAX_GPU_PER_SERVER)
    throw std::runtime_error("native source dimensions exceeded");
  std::ifstream input(argv[4]);
  std::vector<uint64_t> data(n*g*n*g);
  for(auto& x:data) if(!(input>>x)) throw std::runtime_error("matrix truncated");
  std::vector<LocalScheduler> locals(n);
  Matrix server;
  init_matrix(&server,n);
  for(int s=0;s<n;++s) {
    init_local_scheduler(&locals[s],data.data()+s*g*n*g,g,n,s);
    for(int d=0;d<n;++d) set_matrix(&server,locals[s].server2server_data[d],s,d);
  }
  FastAll2All ata;
  init_fastall2all(&ata,&server);
  to_scaled_doubly_stochastic_matrix_fastall2all(&ata);
  std::vector<int> dc(n);
  for(int s=0;s<n;++s) dc[s]=(argc>=5+n?std::stoi(argv[5+s]):s/2);
  long double lan=(argc>=7+n?std::stold(argv[5+n]):400),wan=(argc>=7+n?std::stold(argv[6+n]):100);
  if(variant=="replay") {
    if(argc!=8+2*n)throw std::runtime_error("replay plan path missing");
    std::ifstream plan(argv[7+2*n]); uint count;
    if(!(plan>>count)||count>MAX_SERVER_NUM_SQUARE)throw std::runtime_error("invalid replay stage count");
    std::vector<uint64_t> residual(n*n);
    for(int s=0;s<n;++s)for(int d=0;d<n;++d)residual[s*n+d]=get_matrix(&ata.SDS_mat,s,d);
    ata.p_sets_n=count;
    for(uint k=0;k<count;++k){
      uint64_t q;if(!(plan>>q)||!q)throw std::runtime_error("invalid replay coefficient");
      init_permutation_set(&ata.p_sets[k],q,1,n);std::vector<bool> used(n);
      for(int s=0;s<n;++s){int d;
        if(!(plan>>d)||d<0||d>=n||used[d]||q>residual[s*n+d])
          throw std::runtime_error("invalid replay matching or residual");
        used[d]=true;residual[s*n+d]-=q;map_insert(ata.p_sets[k].mp,&ata.p_sets[k].mp_n,s,d);
      }
      // Export in supplied execution order, with no implicit frequency sorting.
      ata.p_sets_ascending[count-1-k]=k;
    }
    for(auto value:residual)if(value)throw std::runtime_error("replay does not reconstruct padded matrix");
    std::string extra;if(plan>>extra)throw std::runtime_error("trailing replay data");
  } else if(variant=="cost") {
    std::vector<std::vector<uint64_t>> remaining(n,std::vector<uint64_t>(n));
    for(int i=0;i<n;++i)for(int j=0;j<n;++j)remaining[i][j]=get_matrix(&server,i,j);
    uint64_t extracted=0,total=ata.SDS_mat.sdsm_info.max_row_col_sum;
    while(extracted<total) {
      update_edges_fastall2all(&ata);
      for(int s=0;s<n;++s) {
        auto first=ata.hungarian_info.row_to_col[s];
        std::stable_sort(first,first+ata.hungarian_info.row_to_col_n[s],[&](uint a,uint b) {
          auto score=[&](uint col) {int d=col-n;
            return remaining[s][d]*(std::max(lan,wan)/(dc[s]==dc[d]?lan:wan)-1);};
          return score(a)>score(b);
        });
      }
      if(hungarian_fastall2all(&ata)!=uint(n))throw std::runtime_error("imperfect matching");
      bool pending=false,covered=false;
      for(int s=0;s<n;++s) {
        for(int d=0;d<n;++d)pending|=dc[s]!=dc[d]&&remaining[s][d]>0;
        int d=ata.hungarian_info.matching[s]-n;
        covered|=dc[s]!=dc[d]&&remaining[s][d]>0;
      }
      if(wan<lan&&pending&&!covered) {
        std::vector<int> match(n),inverse(n);
        for(int s=0;s<n;++s){match[s]=ata.hungarian_info.matching[s]-n;inverse[match[s]]=s;}
        int u=-1,v=-1;
        for(int s=0;s<n&&u<0;++s)for(uint z=0;z<ata.hungarian_info.row_to_col_n[s];++z){
          int d=ata.hungarian_info.row_to_col[s][z]-n;
          if(dc[s]!=dc[d]&&remaining[s][d]){u=s;v=d;break;}}
        if(u>=0) {
          std::vector<int> parent(n,-2);std::queue<int> queue;
          queue.push(inverse[v]);parent[inverse[v]]=-1;
          while(!queue.empty()&&parent[u]==-2) {
            int x=queue.front();queue.pop();
            for(uint z=0;z<ata.hungarian_info.row_to_col_n[x];++z){
              int next=inverse[ata.hungarian_info.row_to_col[x][z]-n];
              if(parent[next]==-2){parent[next]=x;queue.push(next);}}
          }
          if(parent[u]!=-2) {
            std::vector<int> repaired=match;
            for(int x=u;parent[x]>=0;x=parent[x])repaired[parent[x]]=match[x];
            repaired[u]=v;
            for(int s=0;s<n;++s){ata.hungarian_info.matching[s]=n+repaired[s];ata.hungarian_info.matching[n+repaired[s]]=s;}
          }
        }
      }
      uint64_t amount=update_permutation_sets_fastall2all(&ata);extracted+=amount;
      for(int s=0;s<n;++s){int d=ata.hungarian_info.matching[s]-n;
        remaining[s][d]-=std::min(remaining[s][d],amount);}
    }
    sort_permutation_sets(&ata);
  } else if(variant=="mixed") {
    if(n!=6||g!=1)throw std::runtime_error("manual witness needs6x1");
    int maps[2][6]={{3,0,5,2,1,4},{1,2,3,4,5,0}};
    if(argc>=7+2*n){
      int permutation[6];for(int i=0;i<n;++i)permutation[i]=std::stoi(argv[7+n+i]);
      for(int k=0;k<2;++k){int old[6];std::copy(maps[k],maps[k]+6,old);
        for(int s=0;s<n;++s)maps[k][permutation[s]]=permutation[old[s]];}
    }
    uint64_t amount=get_matrix(&server,0,1);
    ata.p_sets_n=2;
    for(int k=0;k<2;++k){init_permutation_set(&ata.p_sets[k],amount,1,n);
      for(int s=0;s<n;++s)map_insert(ata.p_sets[k].mp,&ata.p_sets[k].mp_n,s,maps[k][s]);}
    if(!verify_decomposition_fastall2all(&ata))throw std::runtime_error("manual witness does not reconstruct demand");
    sort_permutation_sets(&ata);
  } else if(variant=="native")decompose_fastall2all(&ata);
  else throw std::runtime_error("unknown native variant");
  // Original flash_scheduler balances every nonlocal tile (no approximation skip).
  for(int s=0;s<n;++s)for(int d=0;d<n;++d)if(s!=d){
    balance_data_t balance[MAX_GPU_PER_SERVER_SQUARE]={};
    balance_one_server(&locals[s],d,&balance);
    for(int a=0;a<g;++a)for(int b=0;b<g;++b){uint64_t amount=0;
      for(int z=0;z<g;++z)amount+=balance[a*g+b].sz[z];
      flow(s*g+a,s*g+b,amount,"balance",d);}
  }
  for(int s=0;s<n;++s)for(int a=0;a<g;++a)for(int b=0;b<g;++b)
    flow(s*g+a,s*g+b,locals[s].intrinsic_all2all[a*g+b],"intrinsic",0);
  for(uint k=0;k<ata.p_sets_n;++k){
    uint pid=ata.p_sets_ascending[ata.p_sets_n-1-k];
    auto& pset=ata.p_sets[pid];
    std::cout<<"P "<<k<<' '<<pset.frequency;
    for(int s=0;s<n;++s){uint dst;map_lookup(pset.mp,pset.mp_n,s,&dst);std::cout<<' '<<dst;}
    std::cout<<'\n';
    for(int s=0;s<n;++s){uint d;map_lookup(pset.mp,pset.mp_n,s,&d);
      uint64_t channel[MAX_GPU_PER_SERVER][MAX_GPU_PER_SERVER_SQUARE]={},sizes[MAX_GPU_PER_SERVER]={};
      recv_data_t restore[MAX_GPU_PER_SERVER][MAX_GPU_PER_SERVER_SQUARE]={},direct[MAX_GPU_PER_SERVER][MAX_GPU_PER_SERVER_SQUARE]={};
      uint64_t dispatch[MAX_GPU_PER_SERVER][MAX_GPU_PER_SERVER]={};
      restore_one_server(&locals[s],d,&channel,&sizes,&restore,&dispatch,&direct,pset.frequency);
      for(int a=0;a<g;++a){flow(s*g+a,d*g+a,sizes[a],"inter",k);
        if(s!=int(d))for(int b=0;b<g;++b)flow(d*g+a,d*g+b,dispatch[a][b],"dispatch",k);}
    }
  }
  for(auto& local:locals)free_local_scheduler(&local);
  free_fastall2all(&ata);free_matrix(&server);
}

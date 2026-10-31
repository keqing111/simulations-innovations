// Reuse the tested native bridge verbatim; only substitute its planner class.
#include "matching.h"
#include "cached.h"
#include "link_profile.h"
#define Planner CachedPlanner
#include <cstdlib>
link_info_t profile_get_inter_link_info(INTER_LINK_TYPE);
link_info_t profile_get_intra_link_info(INTRA_LINK_TYPE);
static void configure_once(){
  static bool ready=[](){
    const char* profile=std::getenv("FAST_BOTTLENECK_PROFILE");
    configure_link_profile(profile?profile:"paper",400,3600,1);return true;
  }();
  (void)ready;
}
link_info_t get_inter_link_info(INTER_LINK_TYPE type){configure_once();return profile_get_inter_link_info(type);}
link_info_t get_intra_link_info(INTRA_LINK_TYPE type){configure_once();return profile_get_intra_link_info(type);}
// Keep ::main as main: its implicit success return is only valid for that name.
#include "../topology/main.cc"
#undef Planner

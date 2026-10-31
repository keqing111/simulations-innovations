CXX = g++
CXXFLAGS = -std=c++11 -O3 -DNOLOG -include cstring
INCLUDES = -I.. -I../algorithm -I../scheduler -I../topology -I.
NATIVE = ../algorithm/matrix.cpp ../algorithm/all2all.cpp ../scheduler/config.cpp ../scheduler/local.cpp ../scheduler/global.cpp
SHARED = ../topology/model.cpp ../topology/matching.cpp cached.cpp
.PHONY: check
build:
	mkdir -p build
build/model_test: tests/model_test.cc $(SHARED) $(NATIVE) cached.h | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) $< $(SHARED) $(NATIVE) -o $@
build/planner_test: tests/planner_test.cc $(SHARED) $(NATIVE) cached.h | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) $< $(SHARED) $(NATIVE) -o $@
check: build/model_test build/planner_test
	build/model_test
	build/planner_test
	python3 -m unittest discover -s tests -v
